import concurrent.futures
import io
import traceback
import wave
from pydub import AudioSegment
import logging

logger = logging.getLogger('django')


def is_silent_chunk(audio_bytes: bytes, format="wav", silence_thresh_dbfs=-40):
    try:
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=format)
        return audio.dBFS < silence_thresh_dbfs
    except Exception:
        traceback.print_exc()
        return False

def split_audio(audio_bytes, chunk_duration=10):
    """
    Splits audio into strictly 50-second chunks and skips silent ones.
    """
    with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
        frame_rate = wf.getframerate()
        num_channels = wf.getnchannels()
        samp_width = wf.getsampwidth()
        total_frames = wf.getnframes()
        chunk_frames = chunk_duration * frame_rate  # Frames per 50s chunk

        raw_chunks = []
        i = 0
        chunk_number = 0

        while i < total_frames:
            remaining_frames = total_frames - i
            chunk_size = min(chunk_frames, remaining_frames)

            wf.setpos(i)
            chunk_data = wf.readframes(chunk_size)

            output = io.BytesIO()
            with wave.open(output, "wb") as chunk_wf:
                chunk_wf.setnchannels(num_channels)
                chunk_wf.setsampwidth(samp_width)
                chunk_wf.setframerate(frame_rate)
                chunk_wf.writeframes(chunk_data)

            raw_chunks.append((chunk_number, output.getvalue(), chunk_size / frame_rate))
            i += chunk_size
            chunk_number += 1

    # Silence detection (pydub/ffmpeg decode) is the expensive part — run it concurrently
    # instead of blocking the slicing loop above one chunk at a time.
    with concurrent.futures.ThreadPoolExecutor() as executor:
        is_silent_flags = executor.map(lambda c: is_silent_chunk(c[1]), raw_chunks)

        chunks = []
        for (chunk_number, chunk_audio_bytes, chunk_seconds), silent in zip(raw_chunks, is_silent_flags):
            if silent:
                logger.info(f"Skipping silent chunk {chunk_number}")
            else:
                chunk_kb = len(chunk_audio_bytes) / 1024
                logger.info("Chunk %s: %.2f sec, %.2f KB", chunk_number, chunk_seconds, chunk_kb)
                chunks.append((chunk_number, chunk_audio_bytes))

    return chunks
