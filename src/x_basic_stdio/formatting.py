"""Turn raw X JSON into short, readable text for the model.

Models read tool results as text. Compact, consistent lines are cheaper than raw JSON and easier
to reason over. (Returning typed, structured data is a later step in the roadmap.)
"""

from __future__ import annotations

from typing import Any

# Keep results well under what a client will accept in one tool result.
CHARACTER_LIMIT = 20_000


def format_user(user: dict[str, Any]) -> str:
    m = user.get("public_metrics", {})
    lines = [
        f"@{user['username']} ({user.get('name', '')}), id {user['id']}"
        + (" [verified]" if user.get("verified") else ""),
        f"Followers {m.get('followers_count', '?')}, following {m.get('following_count', '?')}, "
        f"posts {m.get('tweet_count', '?')}",
    ]
    if user.get("description"):
        lines.append(f"Bio: {user['description']}")
    if user.get("location"):
        lines.append(f"Location: {user['location']}")
    if user.get("created_at"):
        lines.append(f"Joined: {user['created_at'][:10]}")
    return "\n".join(lines)


def format_post(post: dict[str, Any], authors: dict[str, str]) -> str:
    m = post.get("public_metrics", {})
    handle = authors.get(post.get("author_id", ""), post.get("author_id", "?"))
    # Long posts carry their full text in `note_tweet`; `text` stops at 280 characters.
    full_text = post.get("note_tweet", {}).get("text") or post.get("text", "")
    text = full_text.replace("\n", " ")
    return (
        f"[{post['id']}] @{handle} at {post.get('created_at', '?')}\n"
        f"{text}\n"
        f"likes {m.get('like_count', 0)}, reposts {m.get('retweet_count', 0)}, "
        f"replies {m.get('reply_count', 0)}, quotes {m.get('quote_count', 0)}"
    )


def authors_by_id(body: dict[str, Any]) -> dict[str, str]:
    """Map author id -> @handle from the `includes.users` that `expansions=author_id` adds."""
    return {u["id"]: u["username"] for u in body.get("includes", {}).get("users", [])}


def format_post_page(body: dict[str, Any], empty: str) -> str:
    """Format a list of posts plus the cursor for the next page, if any."""
    posts = body.get("data", [])
    if not posts:
        return empty
    authors = authors_by_id(body)
    blocks = [format_post(p, authors) for p in posts]
    text = join_within_limit(blocks, separator="\n\n")
    next_token = body.get("meta", {}).get("next_token")
    if next_token:
        text += f"\n\nMore results: call again with cursor={next_token!r}"
    return text


def format_missing(body: dict[str, Any]) -> str:
    """Describe items X could not return (it lists them in `errors` next to `data`)."""
    missing = [e.get("value") or e.get("resource_id") for e in body.get("errors", [])]
    missing = [str(m) for m in missing if m]
    return f"\n\nNot found: {', '.join(missing)}" if missing else ""


def join_within_limit(blocks: list[str], separator: str) -> str:
    """Join whole items until the limit, then say how many were left out.

    Cutting a string in the middle can break an item (or JSON) in half; dropping whole items
    keeps what is returned correct.
    """
    kept: list[str] = []
    size = 0
    for block in blocks:
        if kept and size + len(block) + len(separator) > CHARACTER_LIMIT:
            break  # always keep the first item, so an oversized one still comes back
        kept.append(block)
        size += len(block) + len(separator)
    text = separator.join(kept)
    dropped = len(blocks) - len(kept)
    if dropped:
        text += f"{separator}({dropped} more omitted to fit the size limit; ask for fewer results)"
    return text
