# Telegram Task Salary Bot

## User flow
1. User sends `/start`.
2. User must join ALL required Telegram channels.
3. User taps `✅ I've Joined`.
4. Bot verifies membership in every required channel.
5. After successful verification, bot shows the editable ₹180 salary claim message and the existing dynamic buttons.
6. User taps `💰 Claim ₹180` and submits a UPI ID.
7. Claim is stored as `pending`.
8. All admins receive a salary claim notification.
9. Admin opens `💰 Salary Claims` and chooses `✅ Accept User` or `❌ Reject`.
10. User receives the editable accepted/rejected status message.

## Admin features retained
- Button Manager: add, edit name, edit content, set image, delete
- Channel Manager: add/remove mandatory channels
- Admin Manager: add/remove admins
- Editable Welcome, Not Joined, Verified, How It Works, Tasks, Guide, Support
- Editable salary claim, pending, accepted and rejected messages
- Editable Claim button text
- Guide image management
- Statistics
- Broadcast
- Preview

## Environment variables
- `BOT_TOKEN` required
- `ADMIN_USER_ID` required
- `DB_PATH` optional, defaults to `bot.db`
- `REQUIRED_CHANNEL_ID` and `CHANNEL_JOIN_URL` are optional legacy migration settings

## Run
```bash
pip install -r requirements.txt
python bot.py
```
