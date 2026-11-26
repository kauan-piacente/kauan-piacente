#!/usr/bin/env python3
"""Generate minimalist monochrome GitHub stats and top-languages SVG cards."""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#ffffff",
        "border": "#e4e4e7",
        "card_bg": "#fafafa",
        "title": "#18181b",
        "text": "#27272a",
        "muted": "#71717a",
        "accent": "#5c64c4",
        "kpi_total_bg": "#f4f5ff",
        "kpi_total_fg": "#5c64c4",
        "kpi_curr_bg": "#f1f7ff",
        "kpi_curr_fg": "#4b7bb5",
        "kpi_long_bg": "#f5f3ff",
        "kpi_long_fg": "#6d5cae",
        "bar_bg": "#e4e4e7",
        "bar_shades": "#7c83db,#6ba3d6,#8b7fd4,#7eb8c9,#9ca3c9",
    },
    "dark": {
        "bg": "#0d1117",
        "border": "#30363d",
        "card_bg": "#161b22",
        "title": "#f0f6fc",
        "text": "#c9d1d9",
        "muted": "#8b949e",
        "accent": "#a5b4fc",
        "kpi_total_bg": "#171a2b",
        "kpi_total_fg": "#a5b4fc",
        "kpi_curr_bg": "#141e2b",
        "kpi_curr_fg": "#93c5fd",
        "kpi_long_bg": "#1c182b",
        "kpi_long_fg": "#c4b5fd",
        "bar_bg": "#21262d",
        "bar_shades": "#a5b4fc,#93c5fd,#c4b5fd,#7dd3fc,#a5b4c8",
    },
}

QUERY_USER_OVERVIEW = """
query($login: String!) {
  user(login: $login) {
    name
    login
    createdAt
    followers {
      totalCount
    }
    contributionsCollection {
      contributionYears
    }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node {
              name
            }
          }
        }
      }
    }
  }
}
"""

QUERY_YEAR_CONTRIBUTIONS = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalPullRequestContributions
      totalIssueContributions
      totalPullRequestReviewContributions
      restrictedContributionsCount
      contributionCalendar {
        totalContributions
        weeks {
          contributionDays {
            date
            contributionCount
          }
        }
      }
    }
  }
}
"""


def graphql_request(query: str, variables: dict[str, Any], token: str) -> dict[str, Any]:
    """Execute a GitHub GraphQL API request using stdlib urllib."""
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        GITHUB_GRAPHQL_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "kauan-piacente-stats-generator",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail}") from exc

    if "errors" in body:
        raise RuntimeError(f"GitHub GraphQL errors: {body['errors']}")
    return body["data"]


def compute_streaks(days: list[tuple[str, int]], today: dt.date) -> dict[str, Any]:
    """Compute current streak, longest streak, and date ranges from sorted (date, count) pairs."""
    if not days:
        return {
            "current_streak": 0,
            "current_start": None,
            "current_end": None,
            "longest_streak": 0,
            "longest_start": None,
            "longest_end": None,
        }

    counts_by_date: dict[dt.date, int] = {}
    for date_str, count in days:
        parsed = dt.date.fromisoformat(date_str)
        counts_by_date[parsed] = max(counts_by_date.get(parsed, 0), count)

    sorted_dates = sorted(d for d in counts_by_date if d <= today)
    if not sorted_dates:
        return {
            "current_streak": 0,
            "current_start": None,
            "current_end": None,
            "longest_streak": 0,
            "longest_start": None,
            "longest_end": None,
        }

    # Longest streak across all recorded days
    longest_len = 0
    longest_start: dt.date | None = None
    longest_end: dt.date | None = None

    run_len = 0
    run_start: dt.date | None = None
    prev_date: dt.date | None = None

    for d in sorted_dates:
        if counts_by_date[d] > 0:
            if prev_date is not None and (d - prev_date).days == 1 and run_len > 0:
                run_len += 1
            else:
                run_len = 1
                run_start = d
            if run_len >= longest_len:
                longest_len = run_len
                longest_start = run_start
                longest_end = d
        else:
            run_len = 0
            run_start = None
        prev_date = d

    # Current streak: allow today to have 0 contributions if yesterday had > 0
    anchor = today if counts_by_date.get(today, 0) > 0 else today - dt.timedelta(days=1)
    current_len = 0
    current_end: dt.date | None = None
    current_start: dt.date | None = None

    cursor = anchor
    while counts_by_date.get(cursor, 0) > 0:
        if current_end is None:
            current_end = cursor
        current_start = cursor
        current_len += 1
        cursor -= dt.timedelta(days=1)

    return {
        "current_streak": current_len,
        "current_start": current_start,
        "current_end": current_end,
        "longest_streak": longest_len,
        "longest_start": longest_start,
        "longest_end": longest_end,
    }


def format_date_range(start: dt.date | None, end: dt.date | None) -> str:
    """Format a compact human-readable date range."""
    if start is None or end is None:
        return "No active streak"
    if start == end:
        return start.strftime("%b %d, %Y")
    if start.year == end.year:
        return f"{start.strftime('%b %d')} - {end.strftime('%b %d, %Y')}"
    return f"{start.strftime('%b %d, %Y')} - {end.strftime('%b %d, %Y')}"


def format_number(value: int) -> str:
    """Format integers cleanly with thousands separators."""
    return f"{value:,}"


def fetch_github_stats(username: str, token: str) -> dict[str, Any]:
    """Fetch all relevant GitHub stats and language breakdown for a user."""
    overview_data = graphql_request(QUERY_USER_OVERVIEW, {"login": username}, token)
    user = overview_data["user"]
    if user is None:
        raise RuntimeError(f"GitHub user '{username}' not found")

    years = sorted(set(user["contributionsCollection"]["contributionYears"]))
    today = dt.datetime.now(dt.timezone.utc).date()
    if today.year not in years:
        years.append(today.year)

    total_contributions = 0
    total_commits = 0
    total_prs = 0
    total_issues = 0
    total_reviews = 0
    calendar_days: dict[str, int] = {}

    for year in years:
        from_iso = f"{year}-01-01T00:00:00Z"
        to_iso = f"{year}-12-31T23:59:59Z"
        yr_data = graphql_request(
            QUERY_YEAR_CONTRIBUTIONS,
            {"login": username, "from": from_iso, "to": to_iso},
            token,
        )
        cc = yr_data["user"]["contributionsCollection"]
        total_commits += cc["totalCommitContributions"] + cc["restrictedContributionsCount"]
        total_prs += cc["totalPullRequestContributions"]
        total_issues += cc["totalIssueContributions"]
        total_reviews += cc["totalPullRequestReviewContributions"]
        total_contributions += cc["contributionCalendar"]["totalContributions"]

        for week in cc["contributionCalendar"]["weeks"]:
            for day in week["contributionDays"]:
                calendar_days[day["date"]] = max(
                    calendar_days.get(day["date"], 0), day["contributionCount"]
                )

    streaks = compute_streaks(sorted(calendar_days.items()), today)

    repos = user["repositories"]["nodes"]
    total_stars = sum(repo["stargazerCount"] for repo in repos)
    lang_sizes: dict[str, int] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            lang_name = edge["node"]["name"]
            lang_sizes[lang_name] = lang_sizes.get(lang_name, 0) + edge["size"]

    total_lang_bytes = sum(lang_sizes.values())
    top_languages = []
    if total_lang_bytes > 0:
        sorted_langs = sorted(lang_sizes.items(), key=lambda item: item[1], reverse=True)
        for name, size in sorted_langs[:5]:
            top_languages.append(
                {
                    "name": name,
                    "percent": round((size / total_lang_bytes) * 100, 1),
                }
            )

    return {
        "username": user["login"],
        "display_name": user["name"] or user["login"],
        "followers": user["followers"]["totalCount"],
        "repositories": user["repositories"]["totalCount"],
        "stars": total_stars,
        "total_contributions": total_contributions,
        "total_commits": total_commits,
        "total_prs": total_prs,
        "total_issues": total_issues,
        "total_reviews": total_reviews,
        "current_streak": streaks["current_streak"],
        "current_range": format_date_range(streaks["current_start"], streaks["current_end"]),
        "longest_streak": streaks["longest_streak"],
        "longest_range": format_date_range(streaks["longest_start"], streaks["longest_end"]),
        "top_languages": top_languages,
        "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d UTC"),
    }


def render_stats_svg(stats: dict[str, Any], theme_name: str) -> str:
    """Render the minimalist monochrome GitHub overview & streak SVG card."""
    t = THEMES[theme_name]
    width = 680
    height = 250

    username = html.escape(f"@{stats['username']}")
    updated = html.escape(f"Updated {stats['updated_at']}")

    total_contrib = format_number(stats["total_contributions"])
    curr_streak = f"{stats['current_streak']}d"
    long_streak = f"{stats['longest_streak']}d"
    curr_range = html.escape(stats["current_range"])
    long_range = html.escape(stats["longest_range"])

    metrics = [
        ("Commits", format_number(stats["total_commits"])),
        ("Pull Requests", format_number(stats["total_prs"])),
        ("Issues", format_number(stats["total_issues"])),
        ("Stars", format_number(stats["stars"])),
        ("Repositories", format_number(stats["repositories"])),
        ("Followers", format_number(stats["followers"])),
    ]

    metric_cells = []
    cols = 3
    cell_w = 200
    start_x = 32
    start_y = 166
    for idx, (label, val) in enumerate(metrics):
        col = idx % cols
        row = idx // cols
        x = start_x + col * cell_w
        y = start_y + row * 34
        metric_cells.append(
            f'<text x="{x}" y="{y}" class="muted">{html.escape(label)}</text>'
            f'<text x="{x + 172}" y="{y}" text-anchor="end" class="metric-val">{html.escape(val)}</text>'
        )

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="GitHub Stats for {html.escape(stats['username'])}">
  <style>
    .title {{ font: 600 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['title']}; }}
    .sub {{ font: 400 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['muted']}; }}
    .kpi-num {{ font: 700 26px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['accent']}; }}
    .kpi-label {{ font: 600 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['text']}; text-transform: uppercase; letter-spacing: 0.6px; }}
    .kpi-sub {{ font: 400 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['muted']}; }}
    .muted {{ font: 400 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['muted']}; }}
    .metric-val {{ font: 600 12px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; fill: {t['text']}; }}
  </style>

  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="{t['bg']}" stroke="{t['border']}"/>

  <!-- Header -->
  <text x="28" y="34" class="title">GitHub Activity &amp; Streaks</text>
  <text x="652" y="34" text-anchor="end" class="sub">{username} · {updated}</text>
  <line x1="28" y1="48" x2="652" y2="48" stroke="{t['border']}" stroke-width="1"/>

  <!-- 3 Streak / Contribution KPIs -->
  <rect x="28" y="62" width="196" height="74" rx="8" fill="{t['kpi_total_bg']}" stroke="{t['border']}"/>
  <text x="126" y="84" text-anchor="middle" class="kpi-label">Total Contributions</text>
  <text x="126" y="114" text-anchor="middle" class="kpi-num" style="fill: {t['kpi_total_fg']}">{html.escape(total_contrib)}</text>
  <text x="126" y="129" text-anchor="middle" class="kpi-sub">All-time</text>

  <rect x="242" y="62" width="196" height="74" rx="8" fill="{t['kpi_curr_bg']}" stroke="{t['border']}"/>
  <text x="340" y="84" text-anchor="middle" class="kpi-label">Current Streak</text>
  <text x="340" y="114" text-anchor="middle" class="kpi-num" style="fill: {t['kpi_curr_fg']}">{html.escape(curr_streak)}</text>
  <text x="340" y="129" text-anchor="middle" class="kpi-sub">{curr_range}</text>

  <rect x="456" y="62" width="196" height="74" rx="8" fill="{t['kpi_long_bg']}" stroke="{t['border']}"/>
  <text x="554" y="84" text-anchor="middle" class="kpi-label">Longest Streak</text>
  <text x="554" y="114" text-anchor="middle" class="kpi-num" style="fill: {t['kpi_long_fg']}">{html.escape(long_streak)}</text>
  <text x="554" y="129" text-anchor="middle" class="kpi-sub">{long_range}</text>

  <!-- Secondary Metrics -->
  <line x1="28" y1="148" x2="652" y2="148" stroke="{t['border']}" stroke-width="1"/>
  {"".join(metric_cells)}
</svg>
"""


def render_languages_svg(stats: dict[str, Any], theme_name: str) -> str:
    """Render the minimalist monochrome Top Languages SVG card."""
    t = THEMES[theme_name]
    shades = t["bar_shades"].split(",")
    width = 680
    height = 148
    bar_x = 28
    bar_y = 66
    bar_w = 624
    bar_h = 10

    langs = stats["top_languages"]
    segments = []
    legend_items = []

    if not langs:
        segments.append(
            f'<rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5" fill="{t["bar_bg"]}"/>'
        )
        legend_items.append(
            f'<text x="28" y="110" class="muted">No public language data available yet.</text>'
        )
    else:
        total_pct = sum(item["percent"] for item in langs) or 100.0
        curr_x = float(bar_x)
        for idx, item in enumerate(langs):
            color = shades[idx % len(shades)]
            seg_w = (item["percent"] / total_pct) * bar_w
            if idx == len(langs) - 1:
                seg_w = max(0.0, (bar_x + bar_w) - curr_x)
            segments.append(
                f'<rect x="{curr_x:.2f}" y="{bar_y}" width="{seg_w:.2f}" height="{bar_h}" fill="{color}"/>'
            )
            curr_x += seg_w

            col_x = 28 + idx * 126
            legend_items.append(
                f'<circle cx="{col_x + 5}" cy="106" r="4" fill="{color}"/>'
                f'<text x="{col_x + 14}" y="110" class="lang-name">{html.escape(item["name"])}</text>'
                f'<text x="{col_x + 14}" y="126" class="lang-pct">{item["percent"]:.1f}%</text>'
            )

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" fill="none" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Top Languages for {html.escape(stats['username'])}">
  <style>
    .title {{ font: 600 15px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['title']}; }}
    .sub {{ font: 400 11px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['muted']}; }}
    .lang-name {{ font: 600 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['text']}; }}
    .lang-pct {{ font: 400 11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; fill: {t['muted']}; }}
    .muted {{ font: 400 12px -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; fill: {t['muted']}; }}
  </style>
  <defs>
    <clipPath id="bar-clip">
      <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5"/>
    </clipPath>
  </defs>

  <rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="10" fill="{t['bg']}" stroke="{t['border']}"/>

  <text x="28" y="34" class="title">Most Used Languages</text>
  <text x="652" y="34" text-anchor="end" class="sub">Across owned repositories</text>
  <line x1="28" y1="48" x2="652" y2="48" stroke="{t['border']}" stroke-width="1"/>

  <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="{bar_h}" rx="5" fill="{t['bar_bg']}"/>
  <g clip-path="url(#bar-clip)">
    {"".join(segments)}
  </g>

  {"".join(legend_items)}
</svg>
"""


def get_mock_stats(username: str) -> dict[str, Any]:
    """Return deterministic sample stats for local preview and testing."""
    return {
        "username": username,
        "display_name": "Kauan Piacente",
        "followers": 24,
        "repositories": 18,
        "stars": 31,
        "total_contributions": 642,
        "total_commits": 518,
        "total_prs": 42,
        "total_issues": 19,
        "total_reviews": 11,
        "current_streak": 12,
        "current_range": "Nov 15 - Nov 26, 2026",
        "longest_streak": 29,
        "longest_range": "Aug 03 - Aug 31, 2026",
        "top_languages": [
            {"name": "Python", "percent": 38.4},
            {"name": "Java", "percent": 27.1},
            {"name": "JavaScript", "percent": 18.6},
            {"name": "Lua", "percent": 9.5},
            {"name": "HTML", "percent": 6.4},
        ],
        "updated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d UTC"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate GitHub stats SVG cards.")
    parser.add_argument(
        "--username",
        default=os.environ.get("GITHUB_USERNAME", "kauan-piacente"),
        help="GitHub username to query",
    )
    parser.add_argument(
        "--out-dir",
        default=os.environ.get("OUT_DIR", "assets"),
        help="Directory where SVG files will be written",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock stats instead of calling the GitHub API",
    )
    args = parser.parse_args()

    if args.mock:
        stats = get_mock_stats(args.username)
    else:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            print("Error: GH_TOKEN or GITHUB_TOKEN environment variable is required.", file=sys.stderr)
            return 1
        stats = fetch_github_stats(args.username, token)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for theme_name in ("light", "dark"):
        (out_dir / f"stats-{theme_name}.svg").write_text(
            render_stats_svg(stats, theme_name), encoding="utf-8"
        )
        (out_dir / f"langs-{theme_name}.svg").write_text(
            render_languages_svg(stats, theme_name), encoding="utf-8"
        )

    print(f"Generated stats SVGs in {out_dir}/ for @{stats['username']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
