"""Embedding generation service using OpenAI."""

import re

import tiktoken
from openai import AsyncOpenAI

from briefly.core.config import get_settings


class EmbeddingService:
    """Generate embeddings for text content."""

    def __init__(self) -> None:
        settings = get_settings()
        self._client = AsyncOpenAI(api_key=settings.openai_api_key)
        self._model = settings.embedding_model
        self._tokenizer = tiktoken.encoding_for_model("gpt-4")
        self._chunk_size = settings.chunk_size_tokens
        self._chunk_overlap = settings.chunk_overlap_tokens

    def count_tokens(self, text: str) -> int:
        """Count tokens in text."""
        return len(self._tokenizer.encode(text))

    def chunk_text(
        self,
        text: str,
        max_tokens: int | None = None,
        overlap: int | None = None,
    ) -> list[str]:
        """
        Split text into overlapping chunks.

        Uses sentence boundaries when possible to maintain coherence.
        """
        max_tokens = max_tokens or self._chunk_size
        overlap = overlap or self._chunk_overlap

        if not text or not text.strip():
            return []

        # If text fits in one chunk, return as-is
        if self.count_tokens(text) <= max_tokens:
            return [text.strip()]

        # Split on sentence boundaries
        sentences = re.split(r'(?<=[.!?])\s+', text)

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_tokens = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_tokens = self.count_tokens(sentence)

            # If single sentence exceeds max, split on words
            if sentence_tokens > max_tokens:
                # Flush current chunk first
                if current_chunk:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                    current_tokens = 0

                # Split long sentence into word chunks
                words = sentence.split()
                word_chunk: list[str] = []
                word_tokens = 0

                for word in words:
                    word_token_count = self.count_tokens(word + " ")
                    if word_tokens + word_token_count > max_tokens:
                        if word_chunk:
                            chunks.append(" ".join(word_chunk))
                        word_chunk = [word]
                        word_tokens = word_token_count
                    else:
                        word_chunk.append(word)
                        word_tokens += word_token_count

                if word_chunk:
                    current_chunk = word_chunk
                    current_tokens = word_tokens
                continue

            # Add sentence to current chunk if it fits
            if current_tokens + sentence_tokens <= max_tokens:
                current_chunk.append(sentence)
                current_tokens += sentence_tokens
            else:
                # Save current chunk
                if current_chunk:
                    chunks.append(" ".join(current_chunk))

                # Start new chunk with overlap from previous
                if overlap > 0 and current_chunk:
                    # Include sentences from end of previous chunk for overlap
                    overlap_chunk: list[str] = []
                    overlap_tokens = 0
                    for s in reversed(current_chunk):
                        s_tokens = self.count_tokens(s)
                        if overlap_tokens + s_tokens <= overlap:
                            overlap_chunk.insert(0, s)
                            overlap_tokens += s_tokens
                        else:
                            break
                    current_chunk = overlap_chunk + [sentence]
                    current_tokens = overlap_tokens + sentence_tokens
                else:
                    current_chunk = [sentence]
                    current_tokens = sentence_tokens

        # Don't forget the last chunk
        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    async def generate_embedding(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        response = await self._client.embeddings.create(
            model=self._model,
            input=text,
        )
        return response.data[0].embedding

    async def generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts (batched)."""
        if not texts:
            return []

        response = await self._client.embeddings.create(
            model=self._model,
            input=texts,
        )
        return [item.embedding for item in response.data]
