//! Go `encoding/base64`: `StdEncoding.EncodeToString` and `DecodeString`,
//! for the source map generator and mapper (inline `data:` source maps) and
//! the API session (file contents sent as base64).
//!
//! PORT: the crate has no base64 dependency. Go's `Decode` is a loop of
//! `decodeQuantum` (base64.go:312) over the standard alphabet, with `=`
//! padding required, not strict, and `\r` and `\n` skipped. Go's 8- and
//! 4-byte fast paths decode valid input the same way and fall back to
//! `decodeQuantum` at the same offset, so the results and the error offsets
//! are equal. Go returns the partial data with the error; the callers use
//! the error alone.

use crate::gostd::{GoError, errors};

// Go: encoding/base64/base64.go:139 (*Encoding).EncodeToString (StdEncoding)
// PERF: (apiperf1) Go `Encode` (base64.go:145) writes each 3 bytes as 4
// bytes of a buffer of the final size; so does this. The text is ASCII, so
// the UTF-8 check at the end is one fast pass. A `String::push` for each
// byte was 5% of an API createSourceFile loop of a 260 KB file.
pub fn std_encoding_encode_to_string(src: &[u8]) -> String {
    const ENCODE_STD: &[u8; 64] =
        b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let encode = |val: u32, shift: u32| ENCODE_STD[((val >> shift) & 0x3F) as usize];
    let mut dst = vec![0u8; src.len().div_ceil(3) * 4];
    let (whole, rest) = src.as_chunks::<3>();
    let (quads, _) = dst.as_chunks_mut::<4>();
    for (quad, &[b0, b1, b2]) in quads.iter_mut().zip(whole) {
        let val = (u32::from(b0) << 16) | (u32::from(b1) << 8) | u32::from(b2);
        *quad = [
            encode(val, 18),
            encode(val, 12),
            encode(val, 6),
            encode(val, 0),
        ];
    }
    // Go: the remaining small block of `Encode`, with `=` padding.
    if let Some(quad) = quads.get_mut(whole.len()) {
        let val = (u32::from(rest[0]) << 16) | rest.get(1).map_or(0, |&b| u32::from(b) << 8);
        *quad = [
            encode(val, 18),
            encode(val, 12),
            if rest.len() == 2 {
                encode(val, 6)
            } else {
                b'='
            },
            b'=',
        ];
    }
    String::from_utf8(dst).expect("base64 text is ASCII")
}

// Go: encoding/base64/base64.go:429 (*Encoding).DecodeString (StdEncoding)
pub fn std_encoding_decode_string(s: &str) -> Result<Vec<u8>, GoError> {
    let src = s.as_bytes();
    let mut dst: Vec<u8> = Vec::with_capacity(src.len() / 4 * 3);
    let mut si = 0usize;
    while si < src.len() {
        si = decode_quantum(&mut dst, src, si)?;
    }
    Ok(dst)
}

/// Go's `StdEncoding.decodeMap`: the 6-bit value of an alphabet byte, or
/// 0xFF.
fn decode_map(c: u8) -> u8 {
    match c {
        b'A'..=b'Z' => c - b'A',
        b'a'..=b'z' => c - b'a' + 26,
        b'0'..=b'9' => c - b'0' + 52,
        b'+' => 62,
        b'/' => 63,
        _ => 0xFF,
    }
}

// Go: encoding/base64/base64.go:303 CorruptInputError.Error
fn corrupt_input_error(offset: usize) -> GoError {
    errors::new(format!("illegal base64 data at input byte {offset}"))
}

// Go: encoding/base64/base64.go:312 decodeQuantum
// PORT: appends the quantum's bytes to `dst` and gives the next source
// offset. Go's `(nsi, n, err)` with a trailing-garbage error after the
// bytes: the bytes are appended, then the error is returned.
fn decode_quantum(dst: &mut Vec<u8>, src: &[u8], mut si: usize) -> Result<usize, GoError> {
    // Decode quantum using the base64 alphabet
    let mut dbuf = [0u8; 4];
    let mut dlen = 4usize;
    let mut err: Option<GoError> = None;

    let mut j = 0usize;
    while j < dbuf.len() {
        if src.len() == si {
            if j == 0 {
                return Ok(si);
            }
            // j == 1, or padding is required (StdEncoding)
            return Err(corrupt_input_error(si - j));
        }
        let input = src[si];
        si += 1;

        let out = decode_map(input);
        if out != 0xFF {
            dbuf[j] = out;
            j += 1;
            continue;
        }

        if input == b'\n' || input == b'\r' {
            continue;
        }

        if input != b'=' {
            return Err(corrupt_input_error(si - 1));
        }

        // We've reached the end and there's padding
        si = skip_padding(src, si, j)?;
        if si < src.len() {
            // trailing garbage
            err = Some(corrupt_input_error(si));
        }
        dlen = j;
        break;
    }

    // Convert 4x 6bit source bytes into 3 bytes
    let val = u32::from(dbuf[0]) << 18
        | u32::from(dbuf[1]) << 12
        | u32::from(dbuf[2]) << 6
        | u32::from(dbuf[3]);
    let bytes = [(val >> 16) as u8, (val >> 8) as u8, val as u8];
    match dlen {
        4 => dst.extend_from_slice(&bytes[..3]),
        3 => dst.extend_from_slice(&bytes[..2]),
        2 => dst.extend_from_slice(&bytes[..1]),
        _ => {}
    }
    match err {
        Some(err) => Err(err),
        None => Ok(si),
    }
}

/// Part of Go `decodeQuantum`: the padding after `j` data bytes, whose
/// first `=` is consumed (`si` is past it). Gives the offset past the
/// padding and the newlines after it.
fn skip_padding(src: &[u8], mut si: usize, j: usize) -> Result<usize, GoError> {
    match j {
        0 | 1 => {
            // incorrect padding
            return Err(corrupt_input_error(si - 1));
        }
        2 => {
            // "==" is expected, the first "=" is already consumed.
            // skip over newlines
            si = skip_newlines(src, si);
            if si == src.len() {
                // not enough padding
                return Err(corrupt_input_error(src.len()));
            }
            if src[si] != b'=' {
                // incorrect padding
                return Err(corrupt_input_error(si - 1));
            }

            si += 1;
        }
        _ => {}
    }

    // skip over newlines
    Ok(skip_newlines(src, si))
}

/// Part of Go `decodeQuantum`: its `for si < len(src) && (src[si] == '\n'
/// || src[si] == '\r') { si++ }` loops.
fn skip_newlines(src: &[u8], mut si: usize) -> usize {
    while si < src.len() && (src[si] == b'\n' || src[si] == b'\r') {
        si += 1;
    }
    si
}

#[cfg(test)]
mod tests {
    use super::std_encoding_decode_string;

    #[test]
    fn decodes_like_go_std_encoding() {
        assert_eq!(std_encoding_decode_string("").unwrap(), b"");
        assert_eq!(std_encoding_decode_string("Zm9vYmFy").unwrap(), b"foobar");
        assert_eq!(std_encoding_decode_string("Zm9vYg==").unwrap(), b"foob");
        assert_eq!(
            std_encoding_decode_string("Zm9v\r\nYmE=").unwrap(),
            b"fooba"
        );
        let error = |s: &str| std_encoding_decode_string(s).unwrap_err().to_string();
        assert_eq!(error("Zm9vYg="), "illegal base64 data at input byte 7");
        assert_eq!(error("Zm9vY"), "illegal base64 data at input byte 4");
        assert_eq!(error("Zm9v!"), "illegal base64 data at input byte 4");
        assert_eq!(error("Zg==Zg=="), "illegal base64 data at input byte 4");
        assert_eq!(error("Z==="), "illegal base64 data at input byte 1");
    }
}
