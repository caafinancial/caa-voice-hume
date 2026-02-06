# CAA Financial Voice Agent - Hume EVI Bridge

Bridges Twilio Media Streams to Hume AI's Empathic Voice Interface (EVI) for real-time voice conversations with natural backchanneling.

## Features

- Real-time voice processing via Hume EVI 3
- Native backchanneling ("uh-huh", "mm-hmm") 
- Audio format conversion (Twilio mulaw ↔ Hume PCM)
- WebSocket bridge architecture

## Environment Variables

```
HUME_API_KEY=your_api_key
HUME_SECRET_KEY=your_secret_key
HUME_CONFIG_ID=your_config_id
PORT=8000
```

## Deployment

### Render.com

1. Create new Web Service
2. Connect this repository
3. Build command: `pip install -r requirements.txt`
4. Start command: `python hume_twilio_bridge.py`
5. Add environment variables

### Twilio Configuration

Point your Twilio phone number's voice webhook to:
```
https://your-app.onrender.com/voice/incoming
```

## License

Proprietary - CAA Financial
