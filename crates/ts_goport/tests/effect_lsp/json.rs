//! A small JSON reader and writer for the language-server answers of the
//! Effect tests: they read diagnostics back out of an answer and print
//! answers indented, one value per line, so a pinned answer diffs by line.

/// The longest array or object that `Json::pretty` keeps on one line.
const PRETTY_INLINE: usize = 72;

/// A JSON value. Numbers keep their text; objects keep their key order.
#[derive(Clone, Debug, PartialEq)]
pub enum Json {
    Null,
    Bool(bool),
    Number(String),
    String(String),
    Array(Vec<Json>),
    Object(Vec<(String, Json)>),
}

impl Json {
    /// Parses `text`, which must be one JSON value.
    pub fn parse(text: &str) -> Json {
        let mut parser = Parser {
            bytes: text.as_bytes(),
            at: 0,
        };
        let value = parser.value();
        parser.skip_space();
        assert_eq!(parser.at, text.len(), "trailing text after JSON: {text}");
        value
    }

    /// The member `key` of an object.
    pub fn get(&self, key: &str) -> Option<&Json> {
        match self {
            Json::Object(members) => members.iter().find(|(k, _)| k == key).map(|(_, v)| v),
            _ => None,
        }
    }

    pub fn as_array(&self) -> &[Json] {
        match self {
            Json::Array(items) => items,
            _ => &[],
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        match self {
            Json::String(text) => Some(text),
            _ => None,
        }
    }

    /// The value on one line, without spaces.
    pub fn compact(&self) -> String {
        let mut out = String::new();
        self.write(&mut out, None);
        out
    }

    /// The value with two-space indents: each array item and object member
    /// on its own line, except in an array or object whose compact form is
    /// at most `PRETTY_INLINE` bytes, which stays on one line.
    pub fn pretty(&self) -> String {
        let mut out = String::new();
        self.write(&mut out, Some(0));
        out
    }

    fn write(&self, out: &mut String, indent: Option<usize>) {
        if indent.is_some() && matches!(self, Json::Array(_) | Json::Object(_)) {
            let compact = self.compact();
            if compact.len() <= PRETTY_INLINE {
                out.push_str(&compact);
                return;
            }
        }
        match self {
            Json::Null => out.push_str("null"),
            Json::Bool(value) => out.push_str(if *value { "true" } else { "false" }),
            Json::Number(text) => out.push_str(text),
            Json::String(text) => write_string(out, text),
            Json::Array(items) => {
                write_list(out, indent, '[', ']', items, |out, item, indent| {
                    item.write(out, indent);
                });
            }
            Json::Object(members) => {
                write_list(
                    out,
                    indent,
                    '{',
                    '}',
                    members,
                    |out, (key, value), indent| {
                        write_string(out, key);
                        out.push(':');
                        if indent.is_some() {
                            out.push(' ');
                        }
                        value.write(out, indent);
                    },
                );
            }
        }
    }
}

/// Writes `items` between `open` and `close`, one per line when `indent` is
/// set.
fn write_list<T>(
    out: &mut String,
    indent: Option<usize>,
    open: char,
    close: char,
    items: &[T],
    write_item: impl Fn(&mut String, &T, Option<usize>),
) {
    out.push(open);
    let inner = indent.map(|n| n + 2);
    for (i, item) in items.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        if let Some(n) = inner {
            out.push('\n');
            out.push_str(&" ".repeat(n));
        }
        write_item(out, item, inner);
    }
    if let (Some(n), false) = (indent, items.is_empty()) {
        out.push('\n');
        out.push_str(&" ".repeat(n));
    }
    out.push(close);
}

fn write_string(out: &mut String, text: &str) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

struct Parser<'a> {
    bytes: &'a [u8],
    at: usize,
}

impl Parser<'_> {
    fn skip_space(&mut self) {
        while self.bytes.get(self.at).is_some_and(u8::is_ascii_whitespace) {
            self.at += 1;
        }
    }

    fn expect(&mut self, byte: u8) {
        self.skip_space();
        assert_eq!(self.bytes.get(self.at), Some(&byte), "JSON at {}", self.at);
        self.at += 1;
    }

    /// Whether the next byte (after space) is `byte`; consumes it if so.
    fn eat(&mut self, byte: u8) -> bool {
        self.skip_space();
        let found = self.bytes.get(self.at) == Some(&byte);
        if found {
            self.at += 1;
        }
        found
    }

    fn value(&mut self) -> Json {
        self.skip_space();
        match self.bytes.get(self.at) {
            Some(b'{') => self.object(),
            Some(b'[') => self.array(),
            Some(b'"') => Json::String(self.string()),
            Some(b't') => self.word("true", Json::Bool(true)),
            Some(b'f') => self.word("false", Json::Bool(false)),
            Some(b'n') => self.word("null", Json::Null),
            _ => self.number(),
        }
    }

    fn word(&mut self, word: &str, value: Json) -> Json {
        assert!(
            self.bytes[self.at..].starts_with(word.as_bytes()),
            "JSON at {}",
            self.at
        );
        self.at += word.len();
        value
    }

    fn number(&mut self) -> Json {
        let start = self.at;
        while self
            .bytes
            .get(self.at)
            .is_some_and(|b| b.is_ascii_digit() || b"+-.eE".contains(b))
        {
            self.at += 1;
        }
        assert!(self.at > start, "JSON at {start}");
        Json::Number(String::from_utf8(self.bytes[start..self.at].to_vec()).expect("ASCII"))
    }

    fn array(&mut self) -> Json {
        self.expect(b'[');
        let mut items = Vec::new();
        if self.eat(b']') {
            return Json::Array(items);
        }
        loop {
            items.push(self.value());
            if self.eat(b']') {
                return Json::Array(items);
            }
            self.expect(b',');
        }
    }

    fn object(&mut self) -> Json {
        self.expect(b'{');
        let mut members = Vec::new();
        if self.eat(b'}') {
            return Json::Object(members);
        }
        loop {
            self.skip_space();
            let key = self.string();
            self.expect(b':');
            members.push((key, self.value()));
            if self.eat(b'}') {
                return Json::Object(members);
            }
            self.expect(b',');
        }
    }

    fn string(&mut self) -> String {
        self.expect(b'"');
        let mut units: Vec<u16> = Vec::new();
        let mut out = String::new();
        loop {
            let byte = self.bytes[self.at];
            if byte != b'\\' || self.bytes[self.at + 1] != b'u' {
                out.push_str(&String::from_utf16(&units).expect("UTF-16 escapes"));
                units.clear();
            }
            match byte {
                b'"' => {
                    self.at += 1;
                    return out;
                }
                b'\\' => {
                    let escape = self.bytes[self.at + 1];
                    self.at += 2;
                    match escape {
                        b'u' => {
                            let hex = std::str::from_utf8(&self.bytes[self.at..self.at + 4])
                                .expect("ASCII");
                            units.push(u16::from_str_radix(hex, 16).expect("a \\u escape"));
                            self.at += 4;
                        }
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        other => out.push(char::from(other)),
                    }
                }
                _ => {
                    let rest = std::str::from_utf8(&self.bytes[self.at..]).expect("UTF-8 JSON");
                    let c = rest.chars().next().expect("a char");
                    out.push(c);
                    self.at += c.len_utf8();
                }
            }
        }
    }
}
