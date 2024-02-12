from http.server import HTTPServer, BaseHTTPRequestHandler
import asyncio
import zlib
import lorem
from random import randint
import base64

class AsyncHTTPRequestHandler(BaseHTTPRequestHandler):
    async def generate_lorem_deflate(self):
        # Generate lorem ipsum paragraphs
        text = lorem.paragraphs(randint(1, 9))

        # Convert the text to bytes
        read_stream = text.encode('utf-8')

        # Compress the data using zlib (deflate)
        compressed_data = zlib.compress(read_stream)

        # Encode compressed data in base64 to ensure it's text-based for HTTP transmission
        encoded_data = base64.b64encode(compressed_data)

        return encoded_data.decode('utf-8')

    def do_GET(self):
        # Execute the async function and wait for result
        result = asyncio.run(self.generate_lorem_deflate())

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes("<html>\n<head><title>Async Function Execution Results</title></head>\n", "utf-8"))
        self.wfile.write(bytes("<body>\n", "utf-8"))
        self.wfile.write(bytes(f"<p>Result: {result}</p>\n", "utf-8"))
        self.wfile.write(bytes("</body>\n</html>", "utf-8"))

def run(server_class=HTTPServer, handler_class=AsyncHTTPRequestHandler, port=8000):
    server_address = ("", port)
    httpd = server_class(server_address, handler_class)
    print(f"Launching server on port {port}...")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass

    httpd.server_close()
    print("\nServer stopped.")

if __name__ == "__main__":
    run()
