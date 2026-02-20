from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
from .config import LINE_CONFIG

# 5-level action → Japanese label + emoji
ACTION_LABELS = {
    "BUY":       "🔴 買い",
    "MILD_BUY":  "🟠 やや買い",
    "HOLD":      "⚪ HOLD",
    "MILD_SELL": "🔵 やや売り",
    "SELL":      "🟢 売り",
}

def send_notification(signal):
    """
    Send LINE notification for actionable signals.
    HOLD is skipped (no notification).
    """
    action = signal['action']

    if action == "HOLD":
        return

    token = LINE_CONFIG['channel_access_token']
    user_id = LINE_CONFIG['user_id']
    dashboard_url = LINE_CONFIG.get('dashboard_url')

    if not token or not user_id:
        print("LINE configuration missing. Skipping notification.")
        return

    label = ACTION_LABELS.get(action, action)

    # --- Build message ---
    lines = [
        f"【{label}】{signal['name']}",
        f"({signal['ticker']})",
        f"────────────",
        f"現在値: {signal['close']:,.0f}円",
        f"上昇確率: {signal['prob_up']:.1%}",
    ]

    if signal.get('limit_price') is not None:
        lines.append(f"指値目安: {signal['limit_price']:,.0f}円")

    if signal.get('stop_loss') is not None:
        lines.append(f"損切目安: {signal['stop_loss']:,.0f}円")

    lines.append(f"理由: {signal['reason']}")
    if dashboard_url:
        lines.append(f"ダッシュボード: {dashboard_url}")

    text = "\n".join(lines)

    # --- Send via LINE Push API ---
    configuration = Configuration(access_token=token)

    try:
        with ApiClient(configuration) as api_client:
            api_instance = MessagingApi(api_client)
            push_message_request = PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
            api_instance.push_message(push_message_request)
            print(f"Notification sent for {signal['ticker']} ({action})")

    except Exception as e:
        print(f"Failed to send LINE notification: {e}")
