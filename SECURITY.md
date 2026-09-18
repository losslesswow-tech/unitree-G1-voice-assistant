# Security Policy

## Sensitive data

Do not commit or report API keys, SSH credentials, Wi-Fi credentials, recorded audio, transcripts, generated speech, or local model files.

The DeepSeek credential must remain in process memory or the local environment. Review logs must contain findings and filenames only, never sensitive values or audio/text payloads.

## Physical robot boundary

The legacy `unitree_g1_voice` package does not connect to a robot. The complete
application in `apps/g1_voice_assistant` can connect to a Unitree G1 only after
the operator explicitly uses its connection controls. It can transmit audio,
change volume, and start audio playback through SSH or the Unitree AudioClient.
Those actions must be performed in a stationary, supervised environment.

Motion, arm, posture, and light control are outside this repository's scope.

Microphone audio is processed locally. Recognized text and recent conversation
history are sent to DeepSeek. When current-information search is used, the
search query is sent to public search providers and the returned summaries are
sent to DeepSeek. Treat all search results as untrusted data.

Do not commit the SenseVoice model, `gui_deps`, SSH passwords, API keys,
recordings, transcripts, logs, `.env` files, or workstation-specific secrets.

## Reporting

Before publishing a security issue, remove credentials, private network details, recordings, and transcripts from reproductions. Report the smallest source-level example that demonstrates the issue.
