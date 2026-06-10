import os
import time
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
from urllib.parse import quote, urljoin
from .config import LINE_CONFIG

# 5-level action → Japanese label + emoji
ACTION_LABELS = {
    "BUY":       "🔴 買い",
    "MILD_BUY":  "🟠 やや買い",
    "HOLD":      "⚪ HOLD",
    "MILD_SELL": "🔵 やや売り",
    "SELL":      "🟢 売り",
}

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def _should_retry(status):
    """Retry on timeout/connection errors (status None) and 429/5xx; give up on 4xx."""
    return status is None or status in RETRYABLE_STATUS


def _backoff_seconds(attempt, base):
    return base * (4 ** (attempt - 1))   # 1s, 4s, 16s, ...


def _push_once(token: str, user_id: str, text: str) -> None:
    """Single LINE push. Raises on failure (caller handles retry)."""
    configuration = Configuration(access_token=token)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).push_message(
            PushMessageRequest(to=user_id, messages=[TextMessage(text=text)]))


def send_line_text(text: str, *, sleep_fn=time.sleep) -> bool:
    """Push one text message with bounded retry. Returns success bool.
    Never raises — the daily pipeline must not die on notification failure."""
    token = LINE_CONFIG["channel_access_token"]
    user_id = LINE_CONFIG["user_id"]
    if not token or not user_id:
        print("LINE configuration missing. Skipping notification.")
        return False
    try:  # a misconfigured env value must not crash the daily pipeline
        max_attempts = int(os.environ.get("TRADER_NOTIFY_RETRY_MAX") or 3)
        base = float(os.environ.get("TRADER_NOTIFY_RETRY_BASE_SEC") or 1.0)
    except (ValueError, TypeError):
        max_attempts, base = 3, 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            _push_once(token, user_id, text)
            return True
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None)
            retry = _should_retry(status) and attempt < max_attempts
            print(f"LINE push failed (attempt {attempt}/{max_attempts}, "
                  f"status={status}): {exc}" + (" — retrying" if retry else ""))
            if not retry:
                return False
            sleep_fn(_backoff_seconds(attempt, base))
    return False


def send_notification(signal):
    """
    Send LINE notification for actionable signals.
    HOLD is skipped (no notification).
    """
    action = signal['action']

    if action == "HOLD":
        return

    dashboard_url = LINE_CONFIG.get('dashboard_url')

    label = ACTION_LABELS.get(action, action)
    close = signal.get("close")

    # --- Build message ---
    lines = [
        f"【{label}】{signal['name']}",
        f"({signal['ticker']})",
        f"────────────",
    ]

    if close is not None:
        lines.append(f"現在値: {close:,.0f}円")
    else:
        lines.append("現在値: 算出不可")

    if signal.get("prob_up") is not None:
        lines.append(f"上昇確率: {signal['prob_up']:.1%}")
    else:
        lines.append("上昇確率: 算出不可")

    if signal.get('limit_price') is not None:
        lines.append(f"指値目安: {signal['limit_price']:,.0f}円")

    if signal.get('stop_loss') is not None:
        lines.append(f"損切目安: {signal['stop_loss']:,.0f}円")

    lines.append(f"理由: {signal['reason']}")
    if dashboard_url:
        base_url = f"{dashboard_url.rstrip('/')}/"
        ticker = quote(signal["ticker"])
        stock_url = urljoin(base_url, f"stocks/{ticker}")
        lines.append(f"銘柄ページ: {stock_url}")

    text = "\n".join(lines)

    # --- Send via shared retry helper ---
    ok = send_line_text(text)
    if ok:
        print(f"Notification sent for {signal['ticker']} ({action})")
