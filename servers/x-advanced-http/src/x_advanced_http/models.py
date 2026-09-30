"""Typed tool results.

Server 01 returned plain text. Here every tool returns one of these models, and the return
annotation becomes the tool's `outputSchema`. The client then gets two channels from one value:
`structured_content` (the object, for the application) and a JSON text block (for the model).

`Field(description=...)` documents outputs the same way it documents inputs.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class User(BaseModel):
    id: str
    username: str = Field(description="Handle without '@'")
    name: str
    bio: str | None = None
    location: str | None = None
    verified: bool = False
    joined: str | None = Field(default=None, description="Account creation date, YYYY-MM-DD")
    followers: int | None = None
    following: int | None = None
    posts: int | None = Field(default=None, description="Total posts by this user")

    @classmethod
    def from_x(cls, data: dict[str, Any]) -> User:
        m = data.get("public_metrics", {})
        return cls(
            id=data["id"],
            username=data["username"],
            name=data.get("name", ""),
            bio=data.get("description") or None,
            location=data.get("location") or None,
            verified=bool(data.get("verified")),
            joined=(data.get("created_at") or "")[:10] or None,
            followers=m.get("followers_count"),
            following=m.get("following_count"),
            posts=m.get("tweet_count"),
        )


class Users(BaseModel):
    users: list[User]
    not_found: list[str] = Field(default_factory=list, description="Handles X could not find")


class Post(BaseModel):
    id: str
    author: str = Field(description="Author handle without '@', or the author ID if unknown")
    created_at: str | None = None
    text: str
    lang: str | None = None
    likes: int = 0
    reposts: int = 0
    replies: int = 0
    quotes: int = 0
    url: str

    @property
    def engagement(self) -> int:
        return self.likes + self.reposts + self.replies + self.quotes

    @classmethod
    def from_x(cls, data: dict[str, Any], authors: dict[str, str]) -> Post:
        m = data.get("public_metrics", {})
        author = authors.get(data.get("author_id", ""), data.get("author_id", "unknown"))
        return cls(
            id=data["id"],
            author=author,
            created_at=data.get("created_at"),
            text=data.get("text", ""),
            lang=data.get("lang"),
            likes=m.get("like_count", 0),
            reposts=m.get("retweet_count", 0),
            replies=m.get("reply_count", 0),
            quotes=m.get("quote_count", 0),
            url=f"https://x.com/{author}/status/{data['id']}",
        )


class Posts(BaseModel):
    posts: list[Post]
    not_found: list[str] = Field(default_factory=list, description="IDs X could not return")


class PostPage(BaseModel):
    posts: list[Post]
    next_cursor: str | None = Field(
        default=None, description="Pass back as `cursor` to get the next page; null on the last"
    )


class AuthorCount(BaseModel):
    username: str
    posts: int


class SearchDigest(BaseModel):
    query: str
    posts_scanned: int
    pages_fetched: int
    complete: bool = Field(description="False if more matching posts existed beyond the limit")
    top_posts: list[Post] = Field(description="Most engaging posts, best first")
    top_authors: list[AuthorCount]
    languages: dict[str, int] = Field(description="Post count per language code")


class PostResult(BaseModel):
    status: Literal["posted", "deleted", "cancelled"]
    post_id: str | None = None
    url: str | None = None
    text: str | None = None


def authors_by_id(body: dict[str, Any]) -> dict[str, str]:
    """Map author id -> handle from the `includes.users` that `expansions=author_id` adds."""
    return {u["id"]: u["username"] for u in body.get("includes", {}).get("users", [])}


def missing_items(body: dict[str, Any]) -> list[str]:
    """Items X could not return; it lists them in `errors` next to `data`."""
    missing = [e.get("value") or e.get("resource_id") for e in body.get("errors", [])]
    return [str(m) for m in missing if m]


def post_page(body: dict[str, Any]) -> PostPage:
    authors = authors_by_id(body)
    return PostPage(
        posts=[Post.from_x(p, authors) for p in body.get("data", [])],
        next_cursor=body.get("meta", {}).get("next_token"),
    )
