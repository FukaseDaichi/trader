from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, PushMessageRequest, TextMessage
from .config import LINE_CONFIG

def send_notification(signal):
    """
    Send LINE notification if signal is BUY or SELL.
    """
    if signal['action'] == "HOLD":
        return
    
    token = LINE_CONFIG['channel_access_token']
    user_id = LINE_CONFIG['user_id']
    
    if not token or not user_id:
        print("LINE configuration missing. Skipping notification.")
        return

    # Message Construction
    text = (
        f"【{signal['action']}】{signal['name']} ({signal['ticker']})\n"
        f"----------------\n"
        f"現在値: {signal['close']:,}円\n"
        f"指値案: {signal['limit_price']:,}円\n"
        f"上昇確率: {signal['prob_up']:.1%}\n"
        f"理由: {signal['reason']}"
    )
    
    if signal['action'] == 'BUY' and signal.get('stop_loss'):
         text += f"\n損切目安: {signal['stop_loss']:,}円"

    configuration = Configuration(access_token=token)
    
    try:
        with ApiClient(configuration) as api_client:
            api_instance = MessagingApi(api_client)
            push_message_request = PushMessageRequest(
                to=user_id,
                messages=[TextMessage(text=text)]
            )
            api_instance.push_message(push_message_request)
            print(f"Notification sent for {signal['ticker']}")
            
    except Exception as e:
        print(f"Failed to send LINE notification: {e}")
