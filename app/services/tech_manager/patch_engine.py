"""
Tech Manager — Safe Surgical Patch Engine
محرك التعديل الآمن: لا يكتب ملفاً كاملاً أبداً.
كل تعديل = استبدال نص محدد موجود فعلاً في الملف.

مبادئ الأمان:
1. PROTECTED_FILES: ملفات لا يمكن تعديلها أبداً (فقط تنبيه يدوي)
2. التحقق المزدوج: old_content يجب أن يكون موجوداً قبل وبعد الموافقة
3. Python syntax check: قبل كتابة أي .py
4. Atomic write: كتابة مؤقتة ثم استبدال ذري
5. Backup before every write
6. Rollback: استعادة من النسخة الاحتياطية في أي وقت
"""
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path("/app/app")
PATCHES_DIR  = Path("/app/data/tech_manager_patches")
PROPOSALS_FILE = PATCHES_DIR / "pending_proposals.json"
PATCH_LOG      = PATCHES_DIR / "patch_history.jsonl"

# ── ملفات محمية — لا يُكتب فيها أبداً ──────────────────────────────────
# التعديل عليها يكون عبر propose_env_reminder (تذكير يدوي للمشرف)
PROTECTED_FILES = frozenset({
    "core/config.py",
    "core/security.py",
    "db/session.py",
    "main.py",
    "seed.py",
    "core/deps.py",
})

# ── الحد الأقصى للسطور التي يمكن تعديلها في مرة واحدة ─────────────────
MAX_LINES_PER_PATCH = 30


# ══════════════════════════════════════════════════════════════════════════
# Proposals Storage
# ══════════════════════════════════════════════════════════════════════════

def _ensure_dir():
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)


def load_proposals() -> list[dict]:
    _ensure_dir()
    if not PROPOSALS_FILE.exists():
        return []
    try:
        return json.loads(PROPOSALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_proposals(proposals: list[dict]) -> None:
    _ensure_dir()
    PROPOSALS_FILE.write_text(
        json.dumps(proposals, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


# ══════════════════════════════════════════════════════════════════════════
# Add Proposal
# ══════════════════════════════════════════════════════════════════════════

def add_proposal(
    *,
    title: str,
    description: str,
    file_path: str,
    patch_type: str,          # "line_edit" | "append" | "env_reminder"
    reason: str,
    severity: str = "warning",
    old_content: Optional[str] = None,
    new_content: Optional[str] = None,
) -> dict:
    """
    يُضيف اقتراح تعديل.
    يُجري التحقق المبكر: هل old_content موجود فعلاً؟
    يُرجع الاقتراح أو dict فيه "error".
    """
    # ── حماية الملفات المحظورة ─────────────────────────────────────────
    if file_path in PROTECTED_FILES and patch_type != "env_reminder":
        return {
            "error": (
                f"'{file_path}' is a protected file and cannot be modified directly. "
                f"Use propose_env_reminder to alert the admin to update .env instead."
            )
        }

    # ── التحقق المبكر من old_content ───────────────────────────────────
    if patch_type == "line_edit":
        if not old_content or not old_content.strip():
            return {"error": "propose_line_edit requires non-empty old_content"}
        if not new_content:
            return {"error": "propose_line_edit requires non-empty new_content"}

        target = _resolve_path(file_path)
        if target is None:
            return {"error": f"File not found: {file_path}"}

        current = target.read_text(encoding="utf-8", errors="replace")

        if old_content not in current:
            return {
                "error": (
                    f"old_content not found in {file_path}. "
                    f"Read the file again with read_file and copy the EXACT text."
                )
            }

        occurrences = current.count(old_content)
        if occurrences > 1:
            return {
                "error": (
                    f"old_content appears {occurrences} times in {file_path} — ambiguous. "
                    f"Include more surrounding lines for unique context."
                )
            }

        # حد أقصى للتعديل
        changed_lines = len(new_content.splitlines())
        if changed_lines > MAX_LINES_PER_PATCH:
            return {
                "error": f"Patch too large ({changed_lines} lines). Max {MAX_LINES_PER_PATCH} lines per proposal."
            }

    elif patch_type == "append":
        if not new_content or not new_content.strip():
            return {"error": "propose_append requires non-empty new_content"}

    # ── حفظ الاقتراح ───────────────────────────────────────────────────
    proposal_id = f"fix_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}"
    proposal = {
        "id":          proposal_id,
        "title":       title,
        "description": description,
        "file_path":   file_path,
        "patch_type":  patch_type,   # line_edit | append | env_reminder
        "old_content": old_content,
        "new_content": new_content,
        "reason":      reason,
        "severity":    severity,
        "status":      "pending",
        "created_at":  datetime.utcnow().isoformat(),
        "applied_at":  None,
        "backup_path": None,
        "rolled_back_at": None,
    }

    proposals = load_proposals()
    proposals.append(proposal)
    save_proposals(proposals)

    return {"success": True, "proposal_id": proposal_id, "proposal": proposal}


# ══════════════════════════════════════════════════════════════════════════
# Apply Proposal
# ══════════════════════════════════════════════════════════════════════════

def apply_proposal(proposal_id: str) -> dict:
    """
    يُطبّق اقتراحاً معتمداً:
    1. تحقق مزدوج من old_content
    2. نسخة احتياطية
    3. تعديل جراحي (لا كتابة كاملة)
    4. تحقق من صحة Python
    5. كتابة ذرية
    6. تسجيل في patch_history
    """
    proposals = load_proposals()
    proposal = next((p for p in proposals if p["id"] == proposal_id), None)

    if not proposal:
        raise ValueError(f"Proposal not found: {proposal_id}")
    if proposal["status"] != "pending":
        raise ValueError(f"Proposal is already '{proposal['status']}'")

    patch_type = proposal.get("patch_type", "line_edit")
    file_path  = proposal.get("file_path", "")

    # ── env_reminder: لا تغيير في الملفات ─────────────────────────────
    if patch_type == "env_reminder":
        _update_status(proposals, proposal, "approved")
        return {"success": True, "message": "Reminder acknowledged — no file changes made"}

    # ── تحديد مسار الملف ──────────────────────────────────────────────
    target = _resolve_path(file_path)
    if target is None:
        raise FileNotFoundError(f"Target file not found: {file_path}")

    current_content = target.read_text(encoding="utf-8", errors="replace")

    # ── line_edit ──────────────────────────────────────────────────────
    if patch_type == "line_edit":
        old_content = proposal.get("old_content", "")
        new_content = proposal.get("new_content", "")

        # تحقق مزدوج — الملف قد يكون تغير بين إنشاء الاقتراح والموافقة
        if old_content not in current_content:
            raise ValueError(
                f"Patch conflict in {file_path}: "
                f"old_content no longer matches current file. "
                f"The file may have changed — re-scan required."
            )

        if current_content.count(old_content) > 1:
            raise ValueError(
                f"Ambiguous patch: old_content appears multiple times in {file_path}."
            )

        patched = current_content.replace(old_content, new_content, 1)

    # ── append ─────────────────────────────────────────────────────────
    elif patch_type == "append":
        new_content = proposal.get("new_content", "")

        # لا تُضف محتوى موجوداً بالفعل
        if new_content.strip() in current_content:
            _update_status(proposals, proposal, "skipped")
            return {"success": True, "message": "Content already present — skipped"}

        patched = current_content.rstrip("\n") + "\n" + new_content.strip() + "\n"

    else:
        raise ValueError(f"Unknown patch_type: {patch_type}")

    # ── تحقق من صحة Python ────────────────────────────────────────────
    if file_path.endswith(".py"):
        try:
            compile(patched, file_path, "exec")
        except SyntaxError as ex:
            raise ValueError(
                f"Proposed change creates invalid Python syntax in {file_path}: {ex}"
            )

    # ── نسخة احتياطية ─────────────────────────────────────────────────
    _ensure_dir()
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    base = Path(file_path).stem
    backup_path = PATCHES_DIR / f"{base}_{ts}.bak"
    shutil.copy2(target, backup_path)

    # ── كتابة ذرية ────────────────────────────────────────────────────
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(patched, encoding="utf-8")
    tmp.replace(target)   # ذري على نفس filesystem

    # ── تسجيل في patch_history ─────────────────────────────────────────
    log_entry = {
        "timestamp":   datetime.utcnow().isoformat(),
        "proposal_id": proposal_id,
        "file":        file_path,
        "patch_type":  patch_type,
        "backup":      str(backup_path),
        "size_before": len(current_content),
        "size_after":  len(patched),
        "severity":    proposal.get("severity", "info"),
        "reason":      proposal.get("reason", ""),
    }
    with PATCH_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    # ── تحديث الاقتراح ────────────────────────────────────────────────
    proposal["status"]      = "approved"
    proposal["applied_at"]  = datetime.utcnow().isoformat()
    proposal["backup_path"] = str(backup_path)
    save_proposals(proposals)

    diff_lines = len(patched.splitlines()) - len(current_content.splitlines())
    return {
        "success":     True,
        "message":     f"Applied {patch_type} to {file_path}",
        "backup":      str(backup_path),
        "diff_lines":  diff_lines,
        "size_before": len(current_content),
        "size_after":  len(patched),
    }


# ══════════════════════════════════════════════════════════════════════════
# Rollback
# ══════════════════════════════════════════════════════════════════════════

def rollback_proposal(proposal_id: str) -> dict:
    """يستعيد الملف من النسخة الاحتياطية."""
    proposals = load_proposals()
    proposal = next((p for p in proposals if p["id"] == proposal_id), None)

    if not proposal:
        raise ValueError(f"Proposal not found: {proposal_id}")
    if proposal["status"] not in ("approved",):
        raise ValueError(f"Can only rollback approved proposals (current: {proposal['status']})")

    backup_path = proposal.get("backup_path")
    if not backup_path:
        raise ValueError("No backup path recorded for this proposal")

    bak = Path(backup_path)
    if not bak.exists():
        raise FileNotFoundError(f"Backup file not found: {backup_path}")

    file_path = proposal.get("file_path", "")
    target = _resolve_path(file_path)
    if target is None:
        raise FileNotFoundError(f"Target file not found: {file_path}")

    shutil.copy2(bak, target)

    proposal["status"]          = "rolled_back"
    proposal["rolled_back_at"]  = datetime.utcnow().isoformat()
    save_proposals(proposals)

    return {
        "success": True,
        "message": f"Rolled back {file_path} from {bak.name}",
    }


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════

def _resolve_path(relative: str) -> Optional[Path]:
    """يحاول إيجاد الملف في /app/app ثم في /app."""
    candidates = [
        PROJECT_ROOT / relative,
        Path("/app") / relative,
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def _update_status(proposals: list[dict], proposal: dict, status: str) -> None:
    proposal["status"]     = status
    proposal["applied_at"] = datetime.utcnow().isoformat()
    save_proposals(proposals)
