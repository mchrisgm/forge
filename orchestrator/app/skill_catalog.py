"""Curated catalog of suggested skills, hand-picked from the ECC resource
pack (github.com/affaan-m/ECC, MIT — 285 Claude Code-format skills).

Entries are pure data: the install path is the ordinary git repo+subdir
install in skills_service, so nothing here is fetched until a user installs
an entry. Curation rules: every subdir was verified to exist upstream with
parseable SKILL.md frontmatter, and skills requiring external paid APIs
(exa, fal.ai, X API, firecrawl, Context7-only lookups) or the absent ECC
runtime (ecc-* CLI tools, ECC agents) are excluded. Descriptions are our
own one-liners, not upstream's trigger-phrase blocks.

`name` matches the SKILL.md frontmatter name, which is what the Skill row
is keyed by after install — the routers use that to compute `installed`.
"""

from dataclasses import dataclass

ECC_REPO = "https://github.com/affaan-m/ECC"

CATEGORIES = ("workflow", "languages", "quality", "research", "other")


@dataclass(frozen=True)
class SuggestedSkill:
    name: str
    description: str
    repo: str
    subdir: str
    category: str


def _ecc(name: str, description: str, category: str, subdir: str | None = None) -> SuggestedSkill:
    return SuggestedSkill(
        name=name,
        description=description,
        repo=ECC_REPO,
        subdir=f"skills/{subdir or name}",
        category=category,
    )


CATALOG: tuple[SuggestedSkill, ...] = (
    # ── engineering workflow ────────────────────────────────────────────────
    _ecc(
        "tdd-workflow",
        "Test-driven development loop: failing test first, then implement, with coverage goals.",
        "workflow",
    ),
    _ecc(
        "verification-loop",
        "Verify a coding session's work (build, tests, lint, claims) before declaring it done.",
        "workflow",
    ),
    _ecc(
        "git-workflow",
        "Branching strategies, commit conventions, merge vs rebase, and conflict resolution.",
        "workflow",
    ),
    _ecc(
        "blueprint",
        "Turn a one-line objective into a stepwise plan with self-contained context briefs.",
        "workflow",
    ),
    _ecc(
        "codebase-onboarding",
        "Analyze an unfamiliar codebase into an onboarding guide with architecture map.",
        "workflow",
    ),
    _ecc(
        "code-tour",
        "Step-by-step code walkthroughs anchored to real files and lines, for onboarding or PRs.",
        "workflow",
    ),
    _ecc(
        "e2e-testing",
        "Playwright end-to-end testing: Page Object Model, CI, and flaky-test strategies.",
        "workflow",
    ),
    _ecc(
        "database-migrations",
        "Schema and data migrations with rollbacks and zero-downtime patterns across common ORMs.",
        "workflow",
    ),
    _ecc(
        "deployment-patterns",
        "CI/CD pipelines, containerized deploys, health checks, and rollback strategies.",
        "workflow",
    ),
    # ── language & framework patterns ───────────────────────────────────────
    _ecc(
        "python-patterns",
        "Pythonic idioms, PEP 8, type hints, and structure for maintainable Python.",
        "languages",
    ),
    _ecc(
        "python-testing",
        "pytest strategies: fixtures, mocking, parametrization, and coverage.",
        "languages",
    ),
    _ecc(
        "fastapi-patterns",
        "FastAPI structure, Pydantic v2 schemas, dependency injection, and async handlers.",
        "languages",
    ),
    _ecc(
        "django-patterns",
        "Django architecture, DRF API design, ORM best practices, caching, and middleware.",
        "languages",
    ),
    _ecc(
        "golang-patterns",
        "Idiomatic Go: package layout, interfaces, error handling, and concurrency conventions.",
        "languages",
    ),
    _ecc(
        "golang-testing",
        "Go testing: table-driven tests, subtests, benchmarks, and fuzzing.",
        "languages",
    ),
    _ecc(
        "rust-patterns",
        "Idiomatic Rust: ownership, error handling, traits, and concurrency.",
        "languages",
    ),
    _ecc(
        "rust-testing",
        "Rust unit, integration, async, and property-based testing with mocking and coverage.",
        "languages",
    ),
    _ecc(
        "react-patterns",
        "React 18/19: hooks discipline, server/client boundaries, Suspense, and state.",
        "languages",
    ),
    _ecc(
        "react-testing",
        "React Testing Library with Vitest/Jest, MSW mocking, and accessibility checks.",
        "languages",
    ),
    _ecc(
        "vue-patterns",
        "Vue 3 Composition API, reactivity, Pinia state, Vue Router, and Nuxt SSR patterns.",
        "languages",
    ),
    _ecc(
        "nextjs-turbopack",
        "Next.js 16+ with Turbopack: incremental bundling, caching, and webpack trade-offs.",
        "languages",
    ),
    _ecc(
        "docker-patterns",
        "Dockerfiles and Compose: container security, networking, volumes, multi-service.",
        "languages",
    ),
    _ecc(
        "kubernetes-patterns",
        "Kubernetes workloads, resource limits, RBAC, probes, autoscaling, and kubectl debugging.",
        "languages",
    ),
    _ecc(
        "postgres-patterns",
        "PostgreSQL query optimization, schema design, indexing, and row-level security.",
        "languages",
    ),
    _ecc(
        "redis-patterns",
        "Redis caching strategies, distributed locks, rate limiting, and pub/sub.",
        "languages",
    ),
    # ── code quality & review ───────────────────────────────────────────────
    _ecc(
        "security-review",
        "Security checklist for auth, user input, secrets, API endpoints, and sensitive features.",
        "quality",
    ),
    _ecc(
        "coding-standards",
        "Cross-project conventions for naming, readability, immutability, and code review.",
        "quality",
    ),
    _ecc(
        "error-handling",
        "Typed errors, retries, circuit breakers, and failure messages in TS, Python, Go.",
        "quality",
    ),
    _ecc(
        "api-design",
        "REST API design: resource naming, status codes, pagination, and versioning.",
        "quality",
    ),
    _ecc(
        "architecture-decision-records",
        "Capture architectural decisions as structured ADRs with context and alternatives.",
        "quality",
    ),
    _ecc(
        "accessibility",
        "Build and audit UI against WCAG 2.2 AA: keyboard, contrast, and screen-reader support.",
        "quality",
    ),
    _ecc(
        "production-audit",
        "Local-evidence production readiness audit: what breaks in prod before launch.",
        "quality",
    ),
    _ecc(
        "react-performance",
        "React/Next.js performance rules: waterfalls, bundle size, and re-renders.",
        "quality",
    ),
    # ── research & writing ──────────────────────────────────────────────────
    _ecc(
        "market-research",
        "Market sizing, competitive analysis, and industry intelligence with source attribution.",
        "research",
    ),
    _ecc(
        "article-writing",
        "Long-form articles, guides, and tutorials in a consistent voice with solid structure.",
        "research",
    ),
    _ecc(
        "literature-review",
        "Systematic literature review: search planning, screening, synthesis, citations.",
        "research",
        subdir="scientific-thinking-literature-review",
    ),
    # ── agent discipline & other ────────────────────────────────────────────
    _ecc(
        "mcp-server-patterns",
        "Build MCP servers with the Node/TypeScript SDK: tools, resources, validation, transports.",
        "other",
    ),
    _ecc(
        "context-budget",
        "Audit what is eating the context window (agents, skills, MCP servers) and trim it.",
        "other",
    ),
    _ecc(
        "token-budget-advisor",
        "Let the user choose response depth and token budget before answering.",
        "other",
    ),
    _ecc(
        "eval-harness",
        "Eval-driven development: formal evaluations for agent workflows before trusting changes.",
        "other",
    ),
)


def get_entry(name: str) -> SuggestedSkill | None:
    for entry in CATALOG:
        if entry.name == name:
            return entry
    return None
