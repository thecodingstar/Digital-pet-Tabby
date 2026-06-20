#!/usr/bin/env python3
"""Claude Code hook handler — writes per-session mascot state for the statusline.

Registered on PreToolUse / PostToolUse / UserPromptSubmit / Notification / Stop /
SubagentStop / SessionStart. Reads the hook payload on stdin, maps the event to a
mascot state, and persists it to state/<session_id>.json (read by statusline.py).

Never fails loudly: any error exits 0 so it can't disrupt the session.
"""
import sys, json, os, time

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")

# tools that mean "asking the user", not a normal running tool
QUESTION_TOOLS = {"AskUserQuestion", "ExitPlanMode"}


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {
            "currentState": "idle",
            "lastStateChangedAt": 0.0,
            "lastUpdatedAt": 0.0,
            "lastToolName": "",
            "toolCountInTurn": 0,
            "failedToolCountInTurn": 0,
            "activeSubagentCount": 0,
        }


def _tool_failed(payload):
    resp = payload.get("tool_response")
    if isinstance(resp, dict):
        if resp.get("is_error") or resp.get("error") or resp.get("success") is False:
            return True
    if isinstance(resp, str) and resp.strip().lower().startswith("error"):
        return True
    return False


def main():
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
    except Exception:
        payload = {}

    event = payload.get("hook_event_name") or (sys.argv[1] if len(sys.argv) > 1 else "")
    sid = payload.get("session_id") or ""
    if not sid:
        return

    os.makedirs(STATE_DIR, exist_ok=True)
    path = os.path.join(STATE_DIR, f"{sid}.json")
    st = _load(path)
    prev = st.get("currentState")
    tool = payload.get("tool_name") or ""
    new = prev

    if event == "UserPromptSubmit":
        new = "thinking"
        st["toolCountInTurn"] = 0
        st["failedToolCountInTurn"] = 0
    elif event == "PreToolUse":
        st["lastToolName"] = tool
        st["toolCountInTurn"] = int(st.get("toolCountInTurn", 0)) + 1
        if tool == "Task":
            st["activeSubagentCount"] = int(st.get("activeSubagentCount", 0)) + 1
            new = "subagent_running"
        elif tool in QUESTION_TOOLS:
            new = "question"
        else:
            new = "tool_running"
    elif event == "PostToolUse":
        st["lastToolName"] = tool
        if _tool_failed(payload):
            st["failedToolCountInTurn"] = int(st.get("failedToolCountInTurn", 0)) + 1
            new = "tool_failure"
        else:
            new = "tool_success"
    elif event == "Notification":
        new = "permission"
    elif event == "SubagentStop":
        st["activeSubagentCount"] = max(0, int(st.get("activeSubagentCount", 0)) - 1)
        new = "subagent_running" if st["activeSubagentCount"] > 0 else "thinking"
    elif event == "Stop":
        new = "done"
    elif event == "SessionStart":
        src = payload.get("source") or ""
        new = "auth_success" if src in ("login", "startup") else "idle"

    now = time.time()
    if new != prev:
        st["currentState"] = new
        st["lastStateChangedAt"] = now
    st["lastUpdatedAt"] = now

    tmp = path + f".tmp{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
