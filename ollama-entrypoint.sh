#!/bin/sh
# Start Ollama server in the background
ollama serve &
OLLAMA_PID=$!

# Wait for the server to be ready.
# Explicitly set OLLAMA_HOST so the client commands connect to 127.0.0.1
# and not the server's bind address (0.0.0.0), which is not a valid client target.
echo "Waiting for Ollama to start..."
until OLLAMA_HOST=http://127.0.0.1:11434 ollama list > /dev/null 2>&1; do
  sleep 2
done
echo "Ollama ready."

# Pull the configured model (fast no-op if already present)
MODEL="${OLLAMA_MODEL:-llama3.2:3b}"
echo "Pulling model: $MODEL"
OLLAMA_HOST=http://127.0.0.1:11434 ollama pull "$MODEL"
echo "Model ready."

# Keep container alive and forward signals to ollama serve
wait $OLLAMA_PID
