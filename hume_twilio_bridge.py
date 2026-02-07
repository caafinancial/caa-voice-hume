"""
Hume EVI + Twilio Media Streams Bridge

Bridges Twilio Media Streams (WebSocket) to Hume EVI (WebSocket) for
real-time voice conversations with backchanneling.

Audio Flow:
1. Twilio sends mulaw 8kHz audio via Media Streams
2. Bridge converts to PCM 48kHz for Hume (EVI default sample rate)
3. Hume processes and returns PCM 48kHz audio
4. Bridge converts back to mulaw 8kHz for Twilio
"""

import os
import json
import base64
import asyncio
import logging
from typing import Optional
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import Response

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Hume-Twilio Voice Bridge")

# Hume credentials from environment (with fallback for initial testing)
HUME_API_KEY = os.environ.get("HUME_API_KEY", "xR0HqXgU7ImPkTLfmCCtrLOoetSAfGWAm12RkIS4tcy9Kphk")
HUME_SECRET_KEY = os.environ.get("HUME_SECRET_KEY", "inrvjGAPjbfSr9N1gPus00ZkfEXTTNryqkjf3FfMGLRCpLGXAzdUAqZALAjUCjqR")
HUME_CONFIG_ID = os.environ.get("HUME_CONFIG_ID", "7d7e9c68-a45f-47d5-9c21-a5847bf4248d")

# Hume EVI WebSocket URL
HUME_WS_URL = "wss://api.hume.ai/v0/evi/chat"


def ulaw_to_pcm(ulaw_data: bytes) -> bytes:
    """Convert mu-law to 16-bit PCM."""
    import audioop
    return audioop.ulaw2lin(ulaw_data, 2)


def pcm_to_ulaw(pcm_data: bytes) -> bytes:
    """Convert 16-bit PCM to mu-law."""
    import audioop
    return audioop.lin2ulaw(pcm_data, 2)


def resample(data: bytes, from_rate: int, to_rate: int) -> bytes:
    """Resample audio data."""
    import audioop
    return audioop.ratecv(data, 2, 1, from_rate, to_rate, None)[0]


class HumeTwilioBridge:
    """Bridges Twilio Media Streams to Hume EVI."""
    
    def __init__(self, twilio_ws: WebSocket, config_id: str):
        self.twilio_ws = twilio_ws
        self.config_id = config_id
        self.hume_ws: Optional[websockets.WebSocketClientProtocol] = None
        self.stream_sid: Optional[str] = None
        self.call_sid: Optional[str] = None
        self._running = False
        
    async def connect_hume(self) -> bool:
        """Connect to Hume EVI WebSocket."""
        try:
            headers = {"X-Hume-Api-Key": HUME_API_KEY}
            url = f"{HUME_WS_URL}?config_id={self.config_id}"
            
            self.hume_ws = await websockets.connect(
                url,
                additional_headers=headers,
                ping_interval=20,
                ping_timeout=20,
            )
            logger.info(f"Connected to Hume EVI with config: {self.config_id}")
            
            # Send session settings to configure audio format
            session_settings = {
                "type": "session_settings",
                "audio": {
                    "encoding": "linear16",
                    "sample_rate": 48000,
                    "channels": 1
                }
            }
            await self.hume_ws.send(json.dumps(session_settings))
            logger.info("Sent audio session settings to Hume")
            
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Hume: {e}")
            return False
    
    async def handle_twilio_message(self, message: dict):
        """Process incoming Twilio Media Stream message."""
        event = message.get("event")
        
        if event == "start":
            self.stream_sid = message.get("streamSid")
            self.call_sid = message.get("start", {}).get("callSid")
            logger.info(f"Call started: {self.call_sid}")
            
        elif event == "media":
            payload = message.get("media", {}).get("payload")
            if payload and self.hume_ws:
                # Decode base64 mulaw, convert to PCM 48kHz for Hume EVI
                mulaw_data = base64.b64decode(payload)
                pcm_data = ulaw_to_pcm(mulaw_data)
                pcm_data = resample(pcm_data, 8000, 48000)  # Hume EVI uses 48kHz
                
                audio_message = {
                    "type": "audio_input",
                    "data": base64.b64encode(pcm_data).decode()
                }
                await self.hume_ws.send(json.dumps(audio_message))
                
        elif event == "stop":
            logger.info(f"Call ended: {self.call_sid}")
            self._running = False
    
    async def handle_hume_message(self, message: dict):
        """Process incoming Hume EVI message."""
        msg_type = message.get("type")
        
        if msg_type == "audio_output":
            audio_b64 = message.get("data")
            if audio_b64 and self.stream_sid:
                try:
                    # Convert PCM 48kHz from Hume to mulaw 8kHz for Twilio
                    pcm_data = base64.b64decode(audio_b64)
                    pcm_data = resample(pcm_data, 48000, 8000)  # Hume outputs 48kHz
                    mulaw_data = pcm_to_ulaw(pcm_data)
                    
                    twilio_message = {
                        "event": "media",
                        "streamSid": self.stream_sid,
                        "media": {"payload": base64.b64encode(mulaw_data).decode()}
                    }
                    await self.twilio_ws.send_json(twilio_message)
                except Exception as e:
                    logger.error(f"Audio conversion error: {e}")
                
        elif msg_type == "user_message":
            logger.info(f"User: {message.get('message', {}).get('content')}")
        elif msg_type == "assistant_message":
            logger.info(f"Sarah: {message.get('message', {}).get('content')}")
        elif msg_type == "user_interruption":
            logger.info("User interrupted")
        elif msg_type == "error":
            logger.error(f"Hume error: {message.get('message')} - code: {message.get('code')}")
        else:
            logger.debug(f"Hume message type: {msg_type}")
    
    async def receive_hume_messages(self):
        """Listen for messages from Hume."""
        try:
            async for message in self.hume_ws:
                await self.handle_hume_message(json.loads(message))
        except websockets.exceptions.ConnectionClosed:
            logger.info("Hume connection closed")
        except Exception as e:
            logger.error(f"Error receiving from Hume: {e}")
    
    async def run(self):
        """Main bridge loop."""
        if not await self.connect_hume():
            return
            
        self._running = True
        hume_task = asyncio.create_task(self.receive_hume_messages())
        
        try:
            await self.twilio_ws.accept()
            while self._running:
                try:
                    data = await asyncio.wait_for(self.twilio_ws.receive_text(), timeout=30)
                    await self.handle_twilio_message(json.loads(data))
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error: {e}")
                    break
        finally:
            hume_task.cancel()
            if self.hume_ws:
                await self.hume_ws.close()


@app.get("/")
async def health():
    return {"status": "healthy", "service": "CAA Voice - Hume EVI Bridge"}


@app.get("/debug/hume-test")
async def hume_test():
    """Test Hume EVI connection."""
    import websockets
    try:
        headers = {"X-Hume-Api-Key": HUME_API_KEY}
        url = f"{HUME_WS_URL}?config_id={HUME_CONFIG_ID}"
        
        ws = await websockets.connect(url, additional_headers=headers)
        await ws.close()
        return {"status": "ok", "message": "Hume connection successful", "config_id": HUME_CONFIG_ID}
    except Exception as e:
        return {"status": "error", "message": str(e), "config_id": HUME_CONFIG_ID}


@app.post("/voice/incoming")
async def voice_incoming(request: Request):
    """TwiML endpoint for incoming calls."""
    host = request.headers.get("host", "localhost")
    # Check X-Forwarded-Proto header for reverse proxy (Railway, Render, etc.)
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    protocol = "wss" if forwarded_proto == "https" else "ws"
    
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{protocol}://{host}/voice/stream" />
    </Connect>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


@app.websocket("/voice/stream")
async def voice_stream(websocket: WebSocket):
    """WebSocket endpoint for Twilio Media Streams."""
    bridge = HumeTwilioBridge(websocket, HUME_CONFIG_ID)
    await bridge.run()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
