"""Tests for MLLP framing and ACK parsing."""

import socket

import pytest

from labpipe.common.mllp import END_BLOCK, START_BLOCK, MLLPError, frame, parse_ack, read_frame

MESSAGE = "MSH|^~\\&|LIS|LAB|EHR|HOSP|20260101||ORU^R01^ORU_R01|BMP000001-1|P|2.5.1\rPID|1\r"


def test_frame_wraps_the_message_in_start_and_end_blocks():
    framed = frame(MESSAGE)
    assert framed.startswith(b"\x0b")
    assert framed.endswith(b"\x1c\x0d")
    assert framed[1:-2].decode() == MESSAGE


def test_frame_refuses_a_message_containing_a_framing_byte():
    with pytest.raises(MLLPError):
        frame("MSH|bad\x1cbyte")


def socket_pair_with(data: bytes) -> socket.socket:
    """A connected socket whose peer has already written data and closed."""
    reader, writer = socket.socketpair()
    writer.sendall(data)
    writer.close()
    reader.settimeout(2)
    return reader


def test_read_frame_returns_the_message_unwrapped():
    sock = socket_pair_with(frame(MESSAGE))
    assert read_frame(sock, bytearray()) == MESSAGE


def test_read_frame_keeps_the_next_frame_in_the_buffer():
    # Two frames in one TCP read: the second must survive for the next call.
    sock = socket_pair_with(frame("first") + frame("second"))
    buffer = bytearray()
    assert read_frame(sock, buffer) == "first"
    assert read_frame(sock, buffer) == "second"


def test_read_frame_joins_a_frame_split_across_reads():
    reader, writer = socket.socketpair()
    reader.settimeout(2)
    data = frame(MESSAGE)
    writer.sendall(data[:5])
    buffer = bytearray()
    writer.sendall(data[5:])
    assert read_frame(reader, buffer) == MESSAGE
    writer.close()


def test_read_frame_raises_when_the_peer_closes_mid_frame():
    sock = socket_pair_with(START_BLOCK + b"MSH|half a message")
    with pytest.raises(MLLPError, match="closed"):
        read_frame(sock, bytearray())


def test_read_frame_refuses_bytes_before_the_start_block():
    sock = socket_pair_with(b"junk" + START_BLOCK + b"MSH" + END_BLOCK)
    with pytest.raises(MLLPError, match="0x0B"):
        read_frame(sock, bytearray())


def test_read_frame_times_out_when_nothing_arrives():
    reader, writer = socket.socketpair()
    reader.settimeout(0.1)
    with pytest.raises(TimeoutError):
        read_frame(reader, bytearray())
    writer.close()


ACK = "MSH|^~\\&|EHR|HOSP|LIS|LAB|20260101||ACK^R01^ACK|99|P|2.5.1\rMSA|AA|BMP000001-1\r"


def test_parse_ack_reads_the_code_and_the_control_id():
    ack = parse_ack(ACK)
    assert (ack.code, ack.control_id, ack.text) == ("AA", "BMP000001-1", "")
    assert ack.accepted


def test_parse_ack_reads_the_error_text():
    ack = parse_ack(ACK.replace("MSA|AA|BMP000001-1", "MSA|AE|BMP000001-1|Database down"))
    assert (ack.code, ack.text) == ("AE", "Database down")
    assert not ack.accepted


def test_parse_ack_refuses_a_message_without_msa():
    with pytest.raises(MLLPError, match="no MSA"):
        parse_ack(ACK.split("\r")[0])


def test_parse_ack_refuses_an_unknown_code():
    with pytest.raises(MLLPError, match="unknown"):
        parse_ack(ACK.replace("MSA|AA", "MSA|OK"))


def test_fake_engine_acks_with_the_message_control_id(fake_engine):
    engine = fake_engine()
    with socket.create_connection(("127.0.0.1", engine.port), timeout=2) as sock:
        sock.sendall(frame(MESSAGE))
        ack = parse_ack(read_frame(sock, bytearray()))
    assert ack.control_id == "BMP000001-1"
    assert engine.received == [MESSAGE]
