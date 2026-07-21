import wave

import opuslib

SAMPLE_RATE = 48000
CHANNELS = 1


def _load_opus(audio_file: str, frame_ms: int) -> list[bytes]:
    frame_size = SAMPLE_RATE * frame_ms // 1000

    opus_frames = []
    encoder = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_AUDIO)
    with wave.open(audio_file, "rb") as wav:
        assert wav.getframerate() == SAMPLE_RATE, wav.getframerate()
        assert wav.getnchannels() == CHANNELS, wav.getnchannels()
        assert wav.getsampwidth() == 2, wav.getsampwidth()

        while True:
            pcm = wav.readframes(frame_size)

            if len(pcm) < frame_size * CHANNELS:
                break

            opus_packet = encoder.encode(pcm, frame_size)
            opus_frames.append(opus_packet)

    return opus_frames
