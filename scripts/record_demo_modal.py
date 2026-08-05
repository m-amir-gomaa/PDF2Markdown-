import modal
import os

app = modal.App("pdf2markdown-recorder")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .run_commands("apt-get update", "apt-get install -y ffmpeg")
    .pip_install("playwright")
    .run_commands("playwright install chromium --with-deps")
)

@app.function(image=image, timeout=600)
def record_gif(url: str, pdf_bytes: bytes) -> bytes:
    from playwright.sync_api import sync_playwright
    import subprocess
    import time
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(record_video_dir="/tmp/videos")
        page = context.new_page()
        
        print(f"Navigating to {url}")
        try:
            page.goto(url, timeout=30000, wait_until="domcontentloaded")
        except Exception as e:
            print(f"Goto timed out, but proceeding anyway: {e}")
        time.sleep(2)
        
        # Write PDF to disk to upload
        pdf_path = "/tmp/sample.pdf"
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
            
        print("Leaving credentials blank to use default workspace token...")
        page.fill('input[name="token_id"]', "")
        page.fill('input[name="token_secret"]', "")
        
        print("Uploading PDF...")
        # Since we use a hidden input for the file, we can set input files
        page.set_input_files('input[type="file"]', pdf_path)
        time.sleep(1)
        
        page.click('button[type="submit"]')
        print("Submitted. Waiting for result...")
        
        # Wait for the download button to appear (meaning it's done)
        # Timeout 120s for GPU processing
        page.wait_for_selector('a[href^="/download/"]', timeout=120000)
        
        print("Extraction done! Waiting 3s to capture final state...")
        time.sleep(3)
        
        context.close()
        browser.close()
        
    print("Converting video to GIF...")
    # Find the webm video
    video_files = [f for f in os.listdir("/tmp/videos") if f.endswith(".webm")]
    if not video_files:
        raise Exception("No video was recorded")
        
    webm_path = os.path.join("/tmp/videos", video_files[0])
    gif_path = "/tmp/demo.gif"
    
    # Convert using ffmpeg
    subprocess.run([
        "ffmpeg", "-i", webm_path, 
        "-vf", "fps=15,scale=1024:-1:flags=lanczos,split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse",
        "-loop", "0", gif_path
    ], check=True)
    
    with open(gif_path, "rb") as f:
        return f.read()

if __name__ == "__main__":
    import sys
    pdf_path = sys.argv[1]
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
        
    url = "https://m-amir-gomaa--mineru-pdf-extractor-web-app.modal.run"
    with modal.enable_output():
        with app.run():
            print("Running recording job on Modal...")
            gif_bytes = record_gif.remote(url, pdf_bytes)
            
            with open("demo.gif", "wb") as f:
                f.write(gif_bytes)
            print("Saved to demo.gif")
