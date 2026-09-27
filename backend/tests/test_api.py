"""API-layer tests.

The engine tests (test_engine.py) prove the graph maths. These prove the layer
around it: status codes, validation, transaction boundaries, optimistic
concurrency, and the shape of the response a client actually receives.

The distinction matters. An engine can be perfectly correct and still be
unusable if a refused write leaves half a change behind, or if the client is
never told what moved.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from tests.conftest import START, board, link, make_task, raw_board


class TestBoardEndpoint:
    def test_empty_board_is_valid(self, client: TestClient) -> None:
        response = client.get("/api/board")
        assert response.status_code == 200
        body = response.json()
        assert body == {"tasks": [], "dependencies": [], "critical_path": []}

    def test_board_returns_tasks_dependencies_and_critical_path(
        self, client: TestClient, diamond: dict
    ) -> None:
        body = raw_board(client)
        assert len(body["tasks"]) == 4
        assert len(body["dependencies"]) == 4
        assert body["critical_path"], "critical path should not be empty"

    def test_health_reports_ai_configuration(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body["status"] == "ok"
        assert isinstance(body["ai_enabled"], bool)


class TestTaskCrud:
    def test_create_returns_201_and_the_new_task(self, client: TestClient) -> None:
        task = make_task(client, "New task", days=3)
        assert task["duration_days"] == 3
        assert task["status"] == "BACKLOG"

    def test_end_date_is_derived_by_the_engine_not_the_client(
        self, client: TestClient
    ) -> None:
        """A client never supplies end_date; the engine computes it."""
        task = make_task(client, "Four day task", days=4)
        assert task["end_date"] == (START + timedelta(days=3)).isoformat()

    def test_update_changes_only_the_fields_sent(self, client: TestClient) -> None:
        task = make_task(client, "Original", days=2)
        response = client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": task["version"], "title": "Renamed"},
        )
        assert response.status_code == 200

        after = board(client)["Renamed"]
        assert after["duration_days"] == 2, "duration must survive a title-only edit"

    def test_delete_removes_the_task(self, client: TestClient) -> None:
        task = make_task(client, "Temporary")
        assert client.delete(f"/api/tasks/{task['id']}").status_code == 200
        assert "Temporary" not in board(client)

    def test_unknown_task_returns_404(self, client: TestClient) -> None:
        assert client.patch(
            "/api/tasks/missing", json={"version": 0, "title": "x"}
        ).status_code == 404
        assert client.delete("/api/tasks/missing").status_code == 404


class TestValidation:
    def test_empty_title_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/tasks", json={"title": "", "start_date": START.isoformat()}
        )
        assert response.status_code == 422

    def test_zero_duration_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/tasks",
            json={"title": "x", "start_date": START.isoformat(), "duration_days": 0},
        )
        assert response.status_code == 422

    def test_client_cannot_set_engine_owned_fields(self, client: TestClient) -> None:
        """end_date and dependency_state are the engine's to write.

        The create schema simply has no such fields, so anything a client sends
        is ignored rather than trusted.
        """
        response = client.post(
            "/api/tasks",
            json={
                "title": "Sneaky",
                "start_date": START.isoformat(),
                "duration_days": 2,
                "end_date": "2099-01-01",
                "dependency_state": "READY",
                "version": 999,
            },
        )
        assert response.status_code == 201

        task = board(client)["Sneaky"]
        assert task["end_date"] == (START + timedelta(days=1)).isoformat()
        assert task["version"] != 999

    def test_self_dependency_is_rejected(self, client: TestClient) -> None:
        task = make_task(client, "Lonely")
        response = client.post(
            "/api/dependencies",
            json={"upstream_id": task["id"], "downstream_id": task["id"]},
        )
        assert response.status_code == 422

    def test_dependency_on_a_missing_task_returns_404(self, client: TestClient) -> None:
        task = make_task(client, "Real")
        response = client.post(
            "/api/dependencies",
            json={"upstream_id": "ghost", "downstream_id": task["id"]},
        )
        assert response.status_code == 404


class TestOptimisticConcurrency:
    def test_stale_version_is_refused(self, client: TestClient) -> None:
        """Two tabs: the second edit must not silently overwrite the first."""
        task = make_task(client, "Contested")
        client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": task["version"], "title": "First edit"},
        )

        response = client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": task["version"], "title": "Second edit"},
        )
        assert response.status_code == 409
        assert "changed by someone else" in response.json()["detail"]

    def test_refused_write_changes_nothing(self, client: TestClient) -> None:
        task = make_task(client, "Contested")
        client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": task["version"], "title": "First edit"},
        )
        before = raw_board(client)

        client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": task["version"], "title": "Second edit"},
        )
        assert raw_board(client) == before

    def test_the_edited_task_is_always_returned(self, client: TestClient) -> None:
        """Regression test.

        A move that shifted no dates used to return an empty `affected` list, so
        the client never learned the new version and its next edit was refused
        with a spurious 409. The edited task must always come back.
        """
        task = make_task(client, "Solo")
        response = client.patch(
            f"/api/tasks/{task['id']}/move",
            json={"version": task["version"], "status": "REVIEW", "position": 2.0},
        )
        assert response.status_code == 200

        returned = [t for t in response.json()["affected"] if t["id"] == task["id"]]
        assert returned, "the moved task must be in `affected`"
        assert returned[0]["version"] > task["version"]

        # And the returned version must be immediately usable.
        follow_up = client.patch(
            f"/api/tasks/{task['id']}",
            json={"version": returned[0]["version"], "description": "next edit"},
        )
        assert follow_up.status_code == 200, "no spurious stale-version conflict"


class TestCycleRejection:
    def test_cycle_is_refused_with_409(self, client: TestClient, diamond: dict) -> None:
        response = client.post(
            "/api/dependencies",
            json={"upstream_id": diamond["D"]["id"], "downstream_id": diamond["A"]["id"]},
        )
        assert response.status_code == 409

    def test_rejection_names_the_circular_path_in_titles(
        self, client: TestClient, diamond: dict
    ) -> None:
        """The user must be told WHICH chain is circular."""
        response = client.post(
            "/api/dependencies",
            json={"upstream_id": diamond["D"]["id"], "downstream_id": diamond["A"]["id"]},
        )
        detail = response.json()["detail"]
        assert detail["titles"][0] == detail["titles"][-1], "path must be a loop"
        assert set(detail["titles"]) >= {"A", "D"}

    def test_the_graph_is_byte_identical_after_a_rejection(
        self, client: TestClient, diamond: dict
    ) -> None:
        """The rule from the problem statement, at the API boundary."""
        before = raw_board(client)

        client.post(
            "/api/dependencies",
            json={"upstream_id": diamond["D"]["id"], "downstream_id": diamond["A"]["id"]},
        )

        assert raw_board(client) == before

    def test_duplicate_dependency_is_refused(
        self, client: TestClient, diamond: dict
    ) -> None:
        response = client.post(
            "/api/dependencies",
            json={"upstream_id": diamond["A"]["id"], "downstream_id": diamond["B"]["id"]},
        )
        assert response.status_code == 409


class TestCascadeResponse:
    def test_one_edit_returns_every_task_it_moved(
        self, client: TestClient, diamond: dict
    ) -> None:
        """The client must reconcile the whole cascade in one round trip."""
        response = client.patch(
            f"/api/tasks/{diamond['A']['id']}",
            json={"version": diamond["A"]["version"], "duration_days": 6},
        )
        assert response.status_code == 200

        moved = {t["title"] for t in response.json()["affected"]}
        assert {"A", "B", "C", "D"} <= moved

    def test_no_compounding_through_the_api(
        self, client: TestClient, diamond: dict
    ) -> None:
        """Extending A by 3 days moves D by 3 - not 6 - end to end."""
        client.patch(
            f"/api/tasks/{diamond['A']['id']}",
            json={"version": diamond["A"]["version"], "duration_days": 6},
        )

        after = board(client)
        moved = (
            date.fromisoformat(after["D"]["start_date"])
            - date.fromisoformat(diamond["D"]["start_date"])
        ).days
        assert moved == 3, f"D moved {moved} days; 6 would mean compounding"

    def test_removing_a_dependency_unblocks_the_downstream_task(
        self, client: TestClient
    ) -> None:
        a = make_task(client, "Upstream")
        b = make_task(client, "Downstream")
        result = link(client, a, b)

        assert board(client)["Downstream"]["dependency_state"] == "BLOCKED"

        edge = result["dependencies"][0]
        assert client.delete(f"/api/dependencies/{edge['id']}").status_code == 200
        assert board(client)["Downstream"]["dependency_state"] == "READY"

    def test_deleting_a_task_unblocks_whatever_waited_on_it(
        self, client: TestClient
    ) -> None:
        """Dependents must not be left waiting on a ghost."""
        a = make_task(client, "Upstream")
        b = make_task(client, "Downstream")
        link(client, a, b)

        client.delete(f"/api/tasks/{a['id']}")

        assert board(client)["Downstream"]["dependency_state"] == "READY"
        assert raw_board(client)["dependencies"] == [], "edges go with the task"


class TestRollbackOnRegression:
    def test_moving_a_task_out_of_done_reblocks_its_dependents(
        self, client: TestClient
    ) -> None:
        a = make_task(client, "Prerequisite")
        b = make_task(client, "Dependent")
        link(client, a, b)

        a = board(client)["Prerequisite"]
        client.patch(
            f"/api/tasks/{a['id']}/move",
            json={"version": a["version"], "status": "DONE", "position": 1.0},
        )
        assert board(client)["Dependent"]["dependency_state"] == "READY"

        a = board(client)["Prerequisite"]
        response = client.patch(
            f"/api/tasks/{a['id']}/move",
            json={"version": a["version"], "status": "IN_PROGRESS", "position": 1.0},
        )

        assert board(client)["Dependent"]["dependency_state"] == "BLOCKED"
        # The dependent changed too, so the client learns about it immediately.
        assert any(t["title"] == "Dependent" for t in response.json()["affected"])


class TestSuggestionsEndpoint:
    def test_listing_suggestions_works_with_no_provider(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/suggestions")
        assert response.status_code == 200
        assert response.json()["suggestions"] == []

    def test_accepting_an_unknown_suggestion_returns_404(
        self, client: TestClient
    ) -> None:
        assert client.post("/api/suggestions/missing/accept").status_code == 404
        assert client.post("/api/suggestions/missing/reject").status_code == 404


class TestDryRunPreview:
    """The synopsis promised a dry run: see the blast radius before committing.

    The interesting property is not the numbers - the engine tests already
    cover those - but that asking the question changes nothing.
    """

    def test_preview_reports_what_a_change_would_move(
        self, client: TestClient, diamond: dict
    ) -> None:
        response = client.post(
            f"/api/tasks/{diamond['A']['id']}/preview",
            json={"duration_days": 6},
        )
        assert response.status_code == 200

        moves = {m["title"]: m for m in response.json()["moves"]}
        assert {"B", "C", "D"} <= set(moves), "downstream tasks should be listed"

    def test_preview_respects_no_compounding(
        self, client: TestClient, diamond: dict
    ) -> None:
        """The prediction must match what actually happens: D moves 3, not 6."""
        response = client.post(
            f"/api/tasks/{diamond['A']['id']}/preview",
            json={"duration_days": 6},
        )
        moves = {m["title"]: m for m in response.json()["moves"]}
        assert moves["D"]["shift_days"] == 3

    def test_preview_writes_nothing(self, client: TestClient, diamond: dict) -> None:
        """THE property that makes a dry run a dry run."""
        before = raw_board(client)

        client.post(
            f"/api/tasks/{diamond['A']['id']}/preview",
            json={"duration_days": 60, "start_date": "2027-01-01"},
        )

        assert raw_board(client) == before, "a preview must not touch the board"

    def test_prediction_matches_the_real_edit(
        self, client: TestClient, diamond: dict
    ) -> None:
        """Preview then commit: the board must land exactly where it was promised."""
        predicted = {
            m["title"]: m["to_start"]
            for m in client.post(
                f"/api/tasks/{diamond['A']['id']}/preview",
                json={"duration_days": 6},
            ).json()["moves"]
        }

        client.patch(
            f"/api/tasks/{diamond['A']['id']}",
            json={"version": diamond["A"]["version"], "duration_days": 6},
        )

        actual = board(client)
        for title, promised_start in predicted.items():
            assert actual[title]["start_date"] == promised_start, (
                f"{title} was predicted to start {promised_start} "
                f"but actually starts {actual[title]['start_date']}"
            )

    def test_the_edited_task_is_not_listed_as_an_effect(
        self, client: TestClient, diamond: dict
    ) -> None:
        """A preview answers "what ELSE moves?" - the edited task is the cause."""
        moves = client.post(
            f"/api/tasks/{diamond['A']['id']}/preview", json={"duration_days": 6}
        ).json()["moves"]
        assert all(m["id"] != diamond["A"]["id"] for m in moves)

    def test_an_edit_with_no_downstream_effect_reports_nothing(
        self, client: TestClient
    ) -> None:
        lonely = make_task(client, "Lonely", days=2)
        response = client.post(
            f"/api/tasks/{lonely['id']}/preview", json={"duration_days": 3}
        )
        assert response.json()["moves"] == []

    def test_preview_of_a_missing_task_returns_404(self, client: TestClient) -> None:
        assert client.post("/api/tasks/ghost/preview", json={}).status_code == 404
