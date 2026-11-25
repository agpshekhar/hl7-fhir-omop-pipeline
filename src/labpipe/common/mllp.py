"""MLLP framing and HL7 acknowledgments.

MLLP (Minimal Lower Layer Protocol) is how HL7 v2 travels over TCP. TCP is a stream
of bytes with no message boundaries, so MLLP wraps each message in a start byte (0x0B,
vertical tab) and two end bytes (0x1C file separator, then 0x0D carriage return). The
receiver reads until it sees the end bytes, unwraps the message, and answers with an
HL7 ACK message in the same framing.

The ACK's MSA segment says what happened: MSA-1 is the acknowledgment code (AA
accepted, AE error, AR rejected) and MSA-2 echoes the MSH-10 of the message it
answers, which is how a sender matches an ACK to what it sent.
"""

import socket
from dataclasses import dataclass

START_BLOCK = b"\x0b"
END_BLOCK = b"\x1c\x0d"
ENCODING = "utf-8"

ACK_CODES = {"AA", "AE", "AR", "CA", "CE", "CR"}


class MLLPError(Exception):
    """The peer broke the framing, closed the connection, or sent something unusable."""


def frame(message: str) -> bytes:
    """Wrap one HL7 message (segments joined by \\r) in MLLP start and end blocks."""
    if START_BLOCK.decode() in message or END_BLOCK[:1].decode() in message:
        raise MLLPError("message contains an MLLP framing byte")
    return START_BLOCK + message.encode(ENCODING) + END_BLOCK


def read_frame(sock: socket.socket, buffer: bytearray) -> str:
    """Read one framed message from sock and return it unwrapped.

    TCP may deliver a frame in pieces or several frames at once, so bytes are kept in
    buffer between calls: whatever follows the end block stays there for the next read.
    The socket's own timeout applies to each recv, and socket.timeout is passed up to
    the caller, who decides whether to resend.
    """
    while True:
        end = buffer.find(END_BLOCK)
        if end != -1:
            if not buffer.startswith(START_BLOCK):
                raise MLLPError("frame does not start with 0x0B")
            payload = bytes(buffer[len(START_BLOCK) : end])
            del buffer[: end + len(END_BLOCK)]
            return payload.decode(ENCODING)
        chunk = sock.recv(65536)
        if not chunk:
            raise MLLPError("connection closed before the end of the frame")
        buffer.extend(chunk)


@dataclass(frozen=True)
class Ack:
    """What an HL7 ACK says: the code, the MSH-10 it answers, and any text."""

    code: str
    control_id: str
    text: str

    @property
    def accepted(self) -> bool:
        return self.code in {"AA", "CA"}


def parse_ack(message: str) -> Ack:
    """Read MSA-1, MSA-2 and MSA-3 from an ACK message. Anything else is refused."""
    segments = [s for s in message.replace("\n", "\r").split("\r") if s]
    if not segments or not segments[0].startswith("MSH"):
        raise MLLPError("ACK does not start with an MSH segment")
    separator = segments[0][3]
    for seg in segments:
        fields = seg.split(separator)
        if fields[0] == "MSA":
            fields += [""] * (4 - len(fields))
            code, control_id, text = fields[1], fields[2], fields[3]
            if code not in ACK_CODES:
                raise MLLPError(f"unknown acknowledgment code {code!r}")
            return Ack(code=code, control_id=control_id, text=text)
    raise MLLPError("ACK has no MSA segment")
