# Local Jimeng account pool

> Historical integration notes. Current model capabilities are defined in
> `backend/jimeng.py`; the repository root README describes the current startup
> layout. This private backup includes the modified gateway source at
> `../research-jimeng2api`, superseding older notes below about a separate checkout.

## Multi-person Image References

Canvas video requests now preserve up to nine ordered image references using
`reference_inputs`. Studio validates ownership of every input before charging.
The gateway receives `reference_mode: multi_image`, uploads every image, and
builds `video_gen_inputs[].unified_edit_input.material_list` and `meta_list`.
Chinese image-number mentions are mapped to zero-based material indices;
unmentioned materials remain attached. This mode does not reinterpret multiple
images as first and last frames. Single-reference requests retain first-frame
behavior. Audio/video reference generation is not part of this mode.

Protocol fields were checked against the current Jimeng frontend agreement and
conversion bundles. `work/check_multi_reference.py` covers the request contract.
One real four-image, five-second request was attempted on 2026-11-21. The upstream
returned 4010 requiring account security confirmation before accepting the task;
Studio refunded its charge. Successful generation and identity fidelity remain
unverified until that confirmation is completed. Do not automatically resubmit.

Durations 1-30 are forwarded unchanged; the upstream account/model decides whether
it supports the requested duration. Prior notes below describing a local 4-15
restriction are superseded by this behavior.

Gateway: https://github.com/rsfree/jimeng2api

Inspected revision: `b0cd6afdd109ae4d4e0ee6a6747aeba26dc479a9`.
The separate checkout is at `../research-jimeng2api`. No upstream source is
vendored into Studio. Confirm upstream licensing before redistributing it.

Start from the workspace root with `.venv/Scripts/python.exe work/restart_server.py`.
This starts the gateway on loopback port 8300 and Studio on port 8000.
Gateway dependencies: its `requirements.txt` and `requirements-login.txt`;
install the browser with `python -m playwright install chromium --only-shell`.

Studio administrators manage accounts under the Jimeng pool tab. Douyin QR login
and manual session import are supported. Logging into another browser does not
automatically add that session to the pool. Start a login from the pool tab.

The video model uses the async gateway queue. Studio retains user ownership,
credit accounting, saved results and downloads. Duration is validated at 4-15
seconds. A reference image is passed as the first frame. Reference video input
is not supported by this bridge. Model price is editable in Studio's model tab.

Gateway credentials live in `data/.jimeng-gateway-key`, encrypted using Studio's
key. The gateway database lives in `data/jimeng-pool`. Local upstream patches
encrypt account sessions with a separate `.session-key`, preserve masked account
display, and use Playwright's headless shell as browser fallback. Protect the
entire data directory and encryption keys; do not commit or publish them.
The upstream bootstrap also writes a local first-boot administrator key file.

Global concurrency and per-account concurrency are separate settings. Queue
limits do not change the quota or restrictions of the upstream account.
Known task IDs resume polling after Studio restarts; failed status triggers
Studio's existing refund path. Submission is never retried automatically by
Studio when its outcome is unknown.

Verification: `work/check_jimeng.py` checks authorization and mocked task
contracts. `work/check_jimeng_ui.py` checks the running local admin page without
submitting a generation. Real video generation requires a logged-in account.
