//! Port of Effect-TS/tsgo `internal/keybuilder`: the deterministic keys of
//! service, tag and error declarations.

use crate::effect::etscore::KeyPattern;
use crate::effect::typeparser::*;
use crate::prelude::*;

// Go: keybuilder/keybuilder.go Cyrb53
/// Cyrb53 computes a fast non-cryptographic hash of the input string,
/// producing a 16-character zero-padded hex string from two uint32 halves.
/// This is a Go port of the cyrb53 function from the reference TS implementation.
pub fn cyrb53(str: &str) -> String {
    let mut h1: u32 = 0xdead_beef;
    let mut h2: u32 = 0x41c6_ce57;

    for &b in str.as_bytes() {
        let ch = u32::from(b);
        h1 = imul(h1 ^ ch, 2_654_435_761);
        h2 = imul(h2 ^ ch, 1_597_334_677);
    }

    h1 = imul(h1 ^ (h1 >> 16), 2_246_822_507);
    h1 ^= imul(h2 ^ (h2 >> 13), 3_266_489_909);
    h2 = imul(h2 ^ (h2 >> 16), 2_246_822_507);
    h2 ^= imul(h1 ^ (h1 >> 13), 3_266_489_909);

    format!("{h2:08x}{h1:08x}")
}

// Go: keybuilder/keybuilder.go imul
/// imul emulates JavaScript's Math.imul: 32-bit integer multiplication
/// that discards overflow (keeps only the lower 32 bits).
fn imul(a: u32, b: u32) -> u32 {
    a.wrapping_mul(b)
}

/// Go `strings.ToLower`: `unicode.ToLower` on each rune.
fn strings_to_lower(s: &str) -> String {
    s.chars().map(crate::gostd::unicode::to_lower).collect()
}

// Go: keybuilder/keybuilder.go CreateString
/// CreateString computes the expected key string for a class declaration.
/// It takes the source file name, package name, package directory, class name,
/// and target category, and returns the expected key string using the first
/// matching key pattern. Returns empty string if no pattern matches or if
/// package info is missing.
pub fn create_string(
    source_file_name: &str,
    package_name: &str,
    package_directory: &str,
    class_name: &str,
    target: &str,
    key_patterns: &[KeyPattern],
) -> String {
    if package_name.is_empty() {
        return String::new();
    }

    for key_pattern in key_patterns {
        if key_pattern.target != target {
            continue;
        }

        let only_file_name = key_only_file_name(source_file_name);
        let Some(sub_directory) =
            key_sub_directory(source_file_name, package_directory, key_pattern)
        else {
            continue;
        };

        // Construct parts based on pattern
        let class_name_matches =
            crate::fswatch::pathcompare::equal_fold(&only_file_name, class_name);
        let mut parts: Vec<String> = match key_pattern.pattern.as_str() {
            "package-identifier" => vec![package_name.to_string(), only_file_name],
            // "default" and "default-hashed"
            _ => vec![package_name.to_string(), sub_directory, only_file_name],
        };
        if !class_name_matches {
            parts.push(class_name.to_string());
        }
        let full_key = join_key_parts(parts);

        // Hash if default-hashed pattern
        if key_pattern.pattern == "default-hashed" {
            return cyrb53(&full_key);
        }
        return full_key;
    }

    String::new()
}

/// Go `onlyFileName`: the base name of `source_file_name` without its
/// extension, empty for an `index` file.
fn key_only_file_name(source_file_name: &str) -> String {
    let mut only_file_name: String = match source_file_name.rfind('/') {
        None => source_file_name.to_string(),
        Some(last_index) => source_file_name[last_index + 1..].to_string(),
    };
    if let Some(last_ext_index) = only_file_name.rfind('.') {
        only_file_name.truncate(last_ext_index);
    }
    if strings_to_lower(&only_file_name).ends_with("/index") {
        only_file_name.truncate(only_file_name.len() - 6);
    }
    // The TS reference strips "index" when the filename IS "index" (after removing extension)
    if strings_to_lower(&only_file_name) == "index" {
        only_file_name = String::new();
    }
    if let Some(rest) = only_file_name.strip_prefix('/') {
        only_file_name = rest.to_string();
    }
    only_file_name
}

/// Go `subDirectory`: the directory of `source_file_name` relative to the
/// package directory (with a trailing `/`), less the first of the pattern's
/// leading paths it starts with. `None` outside the package directory.
fn key_sub_directory(
    source_file_name: &str,
    package_directory: &str,
    key_pattern: &KeyPattern,
) -> Option<String> {
    let sub_directory = crate::frontend::tspath::get_directory_path(source_file_name);
    let mut sub_directory = sub_directory.strip_prefix(package_directory)?.to_string();
    if !sub_directory.ends_with('/') {
        sub_directory.push('/');
    }
    if let Some(rest) = sub_directory.strip_prefix('/') {
        sub_directory = rest.to_string();
    }
    for prefix in key_pattern.skip_leading_path.iter().flatten() {
        if let Some(rest) = sub_directory.strip_prefix(prefix.as_str()) {
            sub_directory = rest.to_string();
            break;
        }
    }
    Some(sub_directory)
}

/// The key parts without leading and trailing slashes, joined with `/`
/// (blank parts left out).
fn join_key_parts(parts: Vec<String>) -> String {
    parts
        .iter()
        .map(|part| {
            let p = part.strip_prefix('/').unwrap_or(part);
            p.strip_suffix('/').unwrap_or(p)
        })
        .filter(|part| !part.trim().is_empty())
        .collect::<Vec<_>>()
        .join("/")
}
