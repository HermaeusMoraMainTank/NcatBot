"""Issue API Mixin"""

from __future__ import annotations

from typing import Any, List, Optional

from ncatbot.types.github import GitHubIssueInfo, GitHubLabelInfo


class IssueAPIMixin:
    """Issue 操作"""

    _session: Any
    _base_url: str

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any: ...

    async def create_issue(
        self,
        repo: str,
        title: str,
        body: str = "",
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> GitHubIssueInfo:
        payload: dict = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        data = await self._request("POST", f"/repos/{repo}/issues", json=payload)
        return GitHubIssueInfo.model_validate(data)

    async def update_issue(
        self,
        repo: str,
        issue_number: int,
        *,
        title: Optional[str] = None,
        body: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[List[str]] = None,
        assignees: Optional[List[str]] = None,
    ) -> GitHubIssueInfo:
        payload: dict = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels
        if assignees is not None:
            payload["assignees"] = assignees
        data = await self._request(
            "PATCH", f"/repos/{repo}/issues/{issue_number}", json=payload
        )
        return GitHubIssueInfo.model_validate(data)

    async def close_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        return await self.update_issue(repo, issue_number, state="closed")

    async def reopen_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        return await self.update_issue(repo, issue_number, state="open")

    async def get_issue(self, repo: str, issue_number: int) -> GitHubIssueInfo:
        data = await self._request("GET", f"/repos/{repo}/issues/{issue_number}")
        return GitHubIssueInfo.model_validate(data)

    async def add_labels(
        self, repo: str, issue_number: int, labels: List[str]
    ) -> List[GitHubLabelInfo]:
        data = await self._request(
            "POST",
            f"/repos/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        return [GitHubLabelInfo.model_validate(item) for item in data]

    async def remove_label(self, repo: str, issue_number: int, label: str) -> None:
        await self._request(
            "DELETE", f"/repos/{repo}/issues/{issue_number}/labels/{label}"
        )

    async def set_assignees(
        self, repo: str, issue_number: int, assignees: List[str]
    ) -> GitHubIssueInfo:
        return await self.update_issue(repo, issue_number, assignees=assignees)
