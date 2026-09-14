# Security Policy

## Sensitive data

Do not commit or report API keys, SSH credentials, Wi-Fi credentials, recorded audio, transcripts, generated speech, or local model files.

The DeepSeek credential must remain in process memory or the local environment. Review logs must contain findings and filenames only, never sensitive values or audio/text payloads.

## Physical robot boundary

This package does not authorize robot access. Connecting to a Unitree G1, transmitting audio, changing volume, starting or stopping playback, or performing any firmware/network operation requires a separate, explicit operator approval for the specific action.

Motion, arm, posture, and light control are outside this repository's scope.

## Reporting

Before publishing a security issue, remove credentials, private network details, recordings, and transcripts from reproductions. Report the smallest source-level example that demonstrates the issue.

