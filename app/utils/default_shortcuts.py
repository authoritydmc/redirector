"""
Curated default shortcuts for new installs - company-ready starter pack.
Shown in dashboard banner when DB is empty and in /admin/setup for any admin to install.
Categories: Essentials, Team Namespaces, Dynamic, User-Dynamic.
"""
DEFAULT_SHORTCUTS = [
    # Essentials - static
    {
        "pattern": "google",
        "type": "static",
        "target": "https://google.com",
        "description": "Quick search",
        "category": "Essentials",
        "icon": "fa-google"
    },
    {
        "pattern": "github",
        "type": "static",
        "target": "https://github.com",
        "description": "GitHub home",
        "category": "Essentials",
        "icon": "fa-github"
    },
    {
        "pattern": "docs",
        "type": "static",
        "target": "https://docs.google.com",
        "description": "Google Docs (SSO) — never cached",
        "category": "Essentials",
        "icon": "fa-file-lines"
    },
    {
        "pattern": "drive",
        "type": "static",
        "target": "https://drive.google.com",
        "description": "Google Drive",
        "category": "Essentials",
        "icon": "fa-hard-drive"
    },
    {
        "pattern": "mail",
        "type": "static",
        "target": "https://mail.google.com",
        "description": "Email",
        "category": "Essentials",
        "icon": "fa-envelope"
    },
    {
        "pattern": "calendar",
        "type": "static",
        "target": "https://calendar.google.com",
        "description": "Calendar",
        "category": "Essentials",
        "icon": "fa-calendar"
    },
    # Team namespaces - hierarchical (issue #35)
    {
        "pattern": "eng/docs",
        "type": "static",
        "target": "https://notion.so/eng-docs",
        "description": "Engineering docs",
        "category": "Team Namespaces",
        "icon": "fa-code"
    },
    {
        "pattern": "eng/runbook",
        "type": "static",
        "target": "https://notion.so/eng-runbook",
        "description": "Eng on-call runbook",
        "category": "Team Namespaces",
        "icon": "fa-book"
    },
    {
        "pattern": "hr/handbook",
        "type": "static",
        "target": "https://notion.so/hr-handbook",
        "description": "HR handbook",
        "category": "Team Namespaces",
        "icon": "fa-users"
    },
    {
        "pattern": "design/system",
        "type": "static",
        "target": "https://figma.com/system",
        "description": "Design system",
        "category": "Team Namespaces",
        "icon": "fa-palette"
    },
    # Dynamic - demonstrates {var}
    {
        "pattern": "jira",
        "type": "dynamic",
        "target": "https://jira.company.com/browse/{ticket}",
        "description": "Jira ticket — use: r/jira/PROJ-123",
        "category": "Dynamic",
        "icon": "fa-ticket"
    },
    {
        "pattern": "gh",
        "type": "dynamic",
        "target": "https://github.com/company/{repo}",
        "description": "GitHub repo — use: r/gh/my-service",
        "category": "Dynamic",
        "icon": "fa-code-branch"
    },
    # User-dynamic - demonstrates [var] stored per-browser (issue #33)
    {
        "pattern": "my-prs",
        "type": "user-dynamic",
        "target": "https://github.com/pulls?q=author:[username]",
        "description": "My PRs — first visit prompts for [username], saved in browser",
        "category": "User-Dynamic",
        "icon": "fa-user",
        "params": {"username": "Your GitHub username"}
    },
    {
        "pattern": "my-tasks",
        "type": "user-dynamic",
        "target": "https://jira.company.com/issues/?assignee=[email]",
        "description": "My Jira tasks — prompts for [email]",
        "category": "User-Dynamic",
        "icon": "fa-list-check",
        "params": {"email": "Your work email"}
    },
]

def get_defaults_grouped():
    grouped = {}
    for s in DEFAULT_SHORTCUTS:
        cat = s.get("category", "Other")
        grouped.setdefault(cat, []).append(s)
    return grouped

def get_default_by_pattern(pattern: str):
    for s in DEFAULT_SHORTCUTS:
        if s["pattern"] == pattern:
            return s
    return None
