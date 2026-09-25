-- CreateEnum
CREATE TYPE "TaskStatus" AS ENUM ('BACKLOG', 'IN_PROGRESS', 'REVIEW', 'DONE');

-- CreateEnum
CREATE TYPE "DependencyState" AS ENUM ('READY', 'BLOCKED');

-- CreateEnum
CREATE TYPE "DependencyOrigin" AS ENUM ('HUMAN', 'AI_ACCEPTED');

-- CreateEnum
CREATE TYPE "SuggestionStatus" AS ENUM ('PENDING', 'ACCEPTED', 'REJECTED');

-- CreateTable
CREATE TABLE "Task" (
    "id" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT NOT NULL DEFAULT '',
    "status" "TaskStatus" NOT NULL DEFAULT 'BACKLOG',
    "startDate" DATE NOT NULL,
    "endDate" DATE NOT NULL,
    "durationDays" INTEGER NOT NULL DEFAULT 1,
    "pinned" BOOLEAN NOT NULL DEFAULT false,
    "position" DOUBLE PRECISION NOT NULL,
    "dependencyState" "DependencyState" NOT NULL DEFAULT 'READY',
    "version" INTEGER NOT NULL DEFAULT 0,
    "bindingConstraintId" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Task_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Dependency" (
    "id" TEXT NOT NULL,
    "upstreamId" TEXT NOT NULL,
    "downstreamId" TEXT NOT NULL,
    "lagDays" INTEGER NOT NULL DEFAULT 0,
    "origin" "DependencyOrigin" NOT NULL DEFAULT 'HUMAN',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Dependency_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "Suggestion" (
    "id" TEXT NOT NULL,
    "upstreamId" TEXT NOT NULL,
    "downstreamId" TEXT NOT NULL,
    "confidence" DOUBLE PRECISION NOT NULL,
    "rationale" TEXT NOT NULL,
    "status" "SuggestionStatus" NOT NULL DEFAULT 'PENDING',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Suggestion_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "Task_status_position_idx" ON "Task"("status", "position");

-- CreateIndex
CREATE INDEX "Dependency_upstreamId_idx" ON "Dependency"("upstreamId");

-- CreateIndex
CREATE INDEX "Dependency_downstreamId_idx" ON "Dependency"("downstreamId");

-- CreateIndex
CREATE UNIQUE INDEX "Dependency_upstreamId_downstreamId_key" ON "Dependency"("upstreamId", "downstreamId");

-- CreateIndex
CREATE INDEX "Suggestion_status_idx" ON "Suggestion"("status");

-- CreateIndex
CREATE UNIQUE INDEX "Suggestion_upstreamId_downstreamId_key" ON "Suggestion"("upstreamId", "downstreamId");

-- AddForeignKey
ALTER TABLE "Dependency" ADD CONSTRAINT "Dependency_upstreamId_fkey" FOREIGN KEY ("upstreamId") REFERENCES "Task"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Dependency" ADD CONSTRAINT "Dependency_downstreamId_fkey" FOREIGN KEY ("downstreamId") REFERENCES "Task"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Suggestion" ADD CONSTRAINT "Suggestion_upstreamId_fkey" FOREIGN KEY ("upstreamId") REFERENCES "Task"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Suggestion" ADD CONSTRAINT "Suggestion_downstreamId_fkey" FOREIGN KEY ("downstreamId") REFERENCES "Task"("id") ON DELETE CASCADE ON UPDATE CASCADE;
