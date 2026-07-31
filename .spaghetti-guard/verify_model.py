#!/usr/bin/env python3
import sys
from sentence_transformers import SentenceTransformer

model_name = "BAAI/bge-m3"
print(f"Testing model ID: {model_name}...")
model = SentenceTransformer(model_name)
vec = model.encode("テスト文章", normalize_embeddings=True)
print(f"Successfully generated vector!")
print(f"Vector dimension: {len(vec)}")
assert len(vec) == 1024, f"Expected 1024, got {len(vec)}"
print(f"Sample vector (first 5 elements): {vec[:5]}")
print("Model verification passed ✅")
