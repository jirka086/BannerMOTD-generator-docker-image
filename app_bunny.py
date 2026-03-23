import os
import json
import base64
import requests
import hashlib
import time
import io
import asyncio
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from PIL import Image

app = FastAPI()

# Magic Container Persistent Storage Settings
PERSISTENT_STORAGE_DIR = os.getenv("PERSISTENT_STORAGE_DIR", "/mnt/storage")
os.makedirs(os.path.join(PERSISTENT_STORAGE_DIR, "uploads"), exist_ok=True)

CACHE_FILE = "global_skin_cache.json"
MAX_WIDTH = 264
MAX_HEIGHT = 16

# Global in-memory cache
global_cache = {}

def load_cache():
    cache_path = os.path.join(PERSISTENT_STORAGE_DIR, CACHE_FILE)
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading cache: {e}")
    return {}

def save_cache(cache_data):
    cache_path = os.path.join(PERSISTENT_STORAGE_DIR, CACHE_FILE)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f)
    except Exception as e:
        print(f"Exception saving cache: {e}")

def save_image(filename, image_bytes):
    image_path = os.path.join(PERSISTENT_STORAGE_DIR, "uploads", filename)
    try:
        with open(image_path, "wb") as f:
            f.write(image_bytes)
    except Exception as e:
        print(f"Exception saving image: {e}")

def get_block_hash(img):
    return hashlib.md5(img.tobytes()).hexdigest()

def strip_metadata(base64_texture):
    try:
        decoded = base64.b64decode(base64_texture).decode('utf-8')
        data = json.loads(decoded)
        minified = {
            "textures": {
                "SKIN": {
                    "url": data['textures']['SKIN']['url']
                }
            }
        }
        return base64.b64encode(json.dumps(minified, separators=(',', ':')).encode('utf-8')).decode('utf-8')
    except Exception as e:
        return base64_texture

@app.on_event("startup")
async def startup_event():
    global global_cache
    global_cache = load_cache()

@app.get("/", response_class=HTMLResponse)
async def index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>MOTD Generator</title>
        <style>
            body { font-family: sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }
            #log { background: #f4f4f4; padding: 10px; height: 300px; overflow-y: auto; font-family: monospace; white-space: pre-wrap; }
            .btn { background: #007bff; color: white; border: none; padding: 10px 15px; cursor: pointer; border-radius: 4px; }
            .btn:disabled { background: #ccc; }
        </style>
    </head>
    <body>
        <h1>Minecraft Banner MOTD Generator</h1>
        <p>Please upload your banner image (Must be exactly 16px high and max 264px wide for optimal results).</p>
        <input type="file" id="imageInput" accept="image/png" />
        <button id="generateBtn" class="btn" onclick="startGeneration()">Generate MOTD</button>
        <div id="progressContainer" style="display:none; margin-top: 20px;">
            <h3>Progress:</h3>
            <div id="log"></div>
            <a id="downloadLink" class="btn" style="display:none; text-decoration:none; margin-top: 10px; display:inline-block;">Download motd.json</a>
        </div>
        <script>
            async function startGeneration() {
                const fileInput = document.getElementById('imageInput');
                if (fileInput.files.length === 0) {
                    alert("Please select a file first.");
                    return;
                }
                
                const file = fileInput.files[0];
                const btn = document.getElementById('generateBtn');
                btn.disabled = true;
                
                document.getElementById('progressContainer').style.display = 'block';
                const logDiv = document.getElementById('log');
                logDiv.innerHTML = "Starting upload...\\n";
                document.getElementById('downloadLink').style.display = 'none';

                const formData = new FormData();
                formData.append('file', file);
                
                try {
                    const response = await fetch('/generate', {
                        method: 'POST',
                        body: formData
                    });
                    
                    if (!response.ok) {
                        const errorText = await response.text();
                        logDiv.innerHTML += `<span style="color:red">Error: ${errorText}</span>\\n`;
                        btn.disabled = false;
                        return;
                    }
                    
                    const reader = response.body.getReader();
                    const decoder = new TextDecoder();
                    let buffer = "";
                    
                    while (true) {
                        const { value, done } = await reader.read();
                        if (done) break;
                        buffer += decoder.decode(value, {stream: true});
                        
                        // Parse Server-Sent Events with proper buffering
                        let messages = buffer.split('\\n\\n');
                        // The last element is either empty string (if it ended with \n\n) or an incomplete chunk
                        buffer = messages.pop(); 
                        
                        for(let msg of messages) {
                            if (msg.startsWith('data: ')) {
                                try {
                                    const jsonString = msg.substring(6).trim();
                                    if (!jsonString) continue;
                                    const data = JSON.parse(jsonString);
                                    if (data.status === 'log') {
                                        logDiv.innerHTML += data.message + "\\n";
                                        logDiv.scrollTop = logDiv.scrollHeight;
                                    } else if (data.status === 'complete') {
                                        logDiv.innerHTML += "Done!\\n";
                                        logDiv.scrollTop = logDiv.scrollHeight;
                                        
                                        // Make downloadable
                                        const blob = new Blob([JSON.stringify(data.motd, null, 0)], {type: "application/json"});
                                        const url = URL.createObjectURL(blob);
                                        const dlLink = document.getElementById('downloadLink');
                                        dlLink.href = url;
                                        dlLink.download = "motd.json";
                                        dlLink.style.display = 'inline-block';
                                        btn.disabled = false;
                                    } else if (data.status === 'error') {
                                        logDiv.innerHTML += `<span style="color:red">${data.message}</span>\\n`;
                                        btn.disabled = false;
                                    }
                                } catch (parseErr) {
                                    console.error("Failed to parse SSE data: ", msg, parseErr);
                                }
                            }
                        }
                    }
                } catch(e) {
                    logDiv.innerHTML += `<span style="color:red">Request failed: ${e}</span>\\n`;
                    btn.disabled = false;
                }
            }
        </script>
    </body>
    </html>
    """

async def generate_motd_stream(file_bytes: bytes, filename: str):
    global global_cache
    
    yield f"data: {json.dumps({'status': 'log', 'message': 'Processing image...'})}\n\n"
    
    try:
        full_img = Image.open(io.BytesIO(file_bytes)).convert('RGBA')
        width, height = full_img.size
        
        if height != 16 or width > 264:
            yield f"data: {json.dumps({'status': 'error', 'message': f'Invalid dimensions ({width}x{height}). Max size supported is 264x16.'})}\n\n"
            return
            
        cols = width // 8
        rows = height // 8
        
        yield f"data: {json.dumps({'status': 'log', 'message': f'Image approved ({width}x{height}). Grid: {cols}x{rows}. Total characters: {cols*rows}'})}\n\n"
        
        extra_components = []
        total = rows * cols
        current = 0
        cache_updated = False
        
        for r in range(rows):
            for c in range(cols):
                current += 1
                left = c * 8
                upper = r * 8
                block = full_img.crop((left, upper, left + 8, upper + 8))
                
                b_hash = get_block_hash(block)
                if b_hash in global_cache:
                    yield f"data: {json.dumps({'status': 'log', 'message': f'[{current}/{total}/{b_hash[:6]}] Loaded chunk from global cache...'})}\n\n"
                    val = global_cache[b_hash]
                else:
                    skin = Image.new('RGBA', (64, 64), (0,0,0,0))
                    skin.paste(block, (8, 8))
                    buf = io.BytesIO()
                    skin.save(buf, format='PNG')
                    buf.seek(0)

                    yield f"data: {json.dumps({'status': 'log', 'message': f'[{current}/{total}] Uploading chunk to MineSkin... (Waiting 6s)'})}\n\n"
                    
                    while True:
                        try:
                            # Using synchronous requests inside an async generator is generally bad for concurrency 
                            # in FastAPI, but works for the logic demonstration. Use aiohttp for complete asynchronicity.
                            resp = requests.post(
                                'https://api.mineskin.org/generate/upload',
                                headers={'User-Agent': 'Premium-Banner-MOTD-Cloud'},
                                files={'file': ('skin.png', buf.getvalue(), 'image/png')}
                            )
                            if resp.status_code == 200:
                                raw_val = resp.json()['data']['texture']['value']
                                val = strip_metadata(raw_val)
                                global_cache[b_hash] = val
                                cache_updated = True
                                yield f"data: {json.dumps({'status': 'log', 'message': ' => Upload Success!'})}\n\n"
                                await asyncio.sleep(6.2)
                                break
                            elif resp.status_code == 429:
                                yield f"data: {json.dumps({'status': 'log', 'message': ' => Rate limited! Waiting 10s...'})}\n\n"
                                await asyncio.sleep(10)
                            else:
                                yield f"data: {json.dumps({'status': 'log', 'message': f' => Error {resp.status_code}! Retrying in 10s...'})}\n\n"
                                await asyncio.sleep(10)
                        except Exception as e:
                            yield f"data: {json.dumps({'status': 'log', 'message': f' => Request failed: {e}. Retrying...'})}\n\n"
                            await asyncio.sleep(10)

                extra_components.append({
                    "hat": True,
                    "player": {
                        "name": "",
                        "properties": [{"name": "textures", "value": val}]
                    }
                })

            if r < rows - 1:
                extra_components.append({"text": ".", "color": "black", "shadow_color": 0, "extra": ["\n"]})
            else:
                extra_components.append({"text": ".", "color": "black", "shadow_color": 0})

        motd_json = {
            "text": "",
            "color": "white",
            "shadow_color": -1,
            "extra": extra_components
        }
        
        if cache_updated:
            yield f"data: {json.dumps({'status': 'log', 'message': 'Saving global cache to persistent storage...'})}\n\n"
            save_cache(global_cache)
            
        yield f"data: {json.dumps({'status': 'complete', 'motd': motd_json})}\n\n"
            
    except Exception as e:
        yield f"data: {json.dumps({'status': 'error', 'message': str(e)})}\n\n"

@app.post("/generate")
async def generate(file: UploadFile = File(...)):
    if not file.filename.lower().endswith('.png'):
        raise HTTPException(status_code=400, detail="Only PNG files are supported.")
        
    file_bytes = await file.read()
    
    # Save original to persistent storage asynchronously or in background
    sanitized_name = f"{int(time.time())}_{file.filename}"
    save_image(sanitized_name, file_bytes)
    
    return StreamingResponse(generate_motd_stream(file_bytes, file.filename), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
