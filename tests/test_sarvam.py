import httpx
import sys
from types import SimpleNamespace

from clinic_broll.providers import sarvam
from clinic_broll.providers.sarvam import _normalise_response


def test_normalises_batch_chunk_timestamps():
    payload = {
        "transcript": "പല്ല് വേദന cavity ആകാം",
        "language_code": "ml-IN",
        "timestamps": {
            "chunks": ["പല്ല് വേദന", "cavity ആകാം"],
            "start_time_seconds": [0.1, 2.4],
            "end_time_seconds": [2.2, 4.8],
        },
    }
    result = _normalise_response(payload, 5.0)
    assert result["phrases"][0]["text"] == "പല്ല് വേദന"
    assert result["phrases"][1]["start"] == 2.4
    assert result["language_code"] == "ml-IN"


def test_normalises_nested_rest_phrase_timestamps():
    payload = {
        "transcript": "hello",
        "timestamps": {
            "words": ["hello"],
            "start_time_seconds": [0.0],
            "end_time_seconds": [0.6],
            "timestamps": {
                "words": ["hello"],
                "start_time_seconds": [0.0],
                "end_time_seconds": [0.6],
            },
        },
    }
    result = _normalise_response(payload, 1.0)
    assert result["words"][0]["word"] == "hello"
    assert result["phrases"][0]["end"] == 0.6


def test_batch_polling_retries_connection_reset(monkeypatch):
    class Status:
        job_state = "Completed"

    class Job:
        calls = 0

        def get_status(self):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ConnectError("connection reset")
            return Status()

    sleeps = []
    monkeypatch.setattr(sarvam.time, "sleep", sleeps.append)
    job = Job()

    result = sarvam._wait_for_batch_job(job)

    assert result.job_state == "Completed"
    assert job.calls == 2
    assert sleeps == [sarvam.SARVAM_POLL_RETRY_DELAYS[0]]


def test_batch_requests_chunk_timestamps(monkeypatch, tmp_path):
    create_kwargs = {}

    class Job:
        def upload_files(self, *, file_paths):
            pass

        def start(self):
            pass

        def get_file_results(self):
            return {"successful": [{}], "failed": []}

        def download_outputs(self, *, output_dir):
            sarvam.write_json(
                tmp_path / "out" / "batch-output" / "speech.wav.json",
                {
                    "transcript": "ഒന്ന്. രണ്ട്.",
                    "timestamps": {
                        "chunks": ["ഒന്ന്.", "രണ്ട്."],
                        "start_time_seconds": [0.0, 1.0],
                        "end_time_seconds": [1.0, 2.0],
                    },
                },
            )

    class SpeechToTextJobs:
        def create_job(self, **kwargs):
            create_kwargs.update(kwargs)
            return Job()

    class Client:
        def __init__(self, **kwargs):
            self.speech_to_text_job = SpeechToTextJobs()

    monkeypatch.setitem(sys.modules, "sarvamai", SimpleNamespace(SarvamAI=Client))
    monkeypatch.setattr(sarvam, "_wait_for_batch_job", lambda job: None)
    monkeypatch.setattr(sarvam, "_duration", lambda audio: 2.0)
    audio = tmp_path / "speech.wav"
    audio.touch()

    result, _, _ = sarvam._batch_transcribe(
        audio=audio,
        output_dir=tmp_path / "out",
        key="test",
        model="saaras:v3",
        mode="codemix",
        language="ml-IN",
    )

    assert create_kwargs["with_timestamps"] is True
    assert len(result["phrases"]) == 2
