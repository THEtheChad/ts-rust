use crate::ls::lsutil::prelude::*;

use crate::flags_macros::go_enum;
use crate::frontend::json::{
    JsonDecoder, JsonError, MarshalerTo, UnmarshalerFrom, json_unmarshal_decode,
};
use crate::modulespecifiers::{
    ImportModuleSpecifierEndingPreference, ImportModuleSpecifierPreference,
};
use std::borrow::Cow;

// Port of Go `ls/lsutil/userpreferences.go`. The parser and serializer that
// ts#64554 generates (`withConfig`, `applyRawPreferences`, `MarshalJSONTo`,
// the `parsePreference<Type>` functions) are in `userpreferences_generated.rs`.
// Go `any` is `LspAny` and Go `map[string]any` is `IndexMap<String, LspAny>`.
// Go `nil` is `LspAny::Null`. A Go `[]string` field is `Option<Vec<String>>`:
// `MarshalJSONTo` skips a nil slice and writes an empty one as `[]`.

// Go: ls/lsutil/userpreferences.go:16 NewDefaultUserPreferences
pub fn new_default_user_preferences() -> UserPreferences {
    UserPreferences {
        format_code_settings: get_default_format_code_settings(),

        include_completions_for_module_exports: Tristate::True,
        include_completions_for_import_statements: Tristate::True,
        enable_auto_closing_tags: Tristate::True,
        enable_js_doc_completions: Tristate::True,
        generate_return_in_doc_template: Tristate::True,

        allow_rename_of_import_path: Tristate::True,
        provide_refactor_not_applicable_reason: Tristate::True,
        enable_formatting: Tristate::True,
        enable_validation: Tristate::True,
        display_parts_for_js_doc: Tristate::True,
        disable_line_text_in_references: Tristate::True,
        report_style_checks_as_warnings: Tristate::True,

        exclude_library_symbols_in_nav_to: Tristate::True,
        workspace_symbols_scope: WorkspaceSymbolsScope::ALL_OPEN_PROJECTS,
        ..Default::default()
    }
}

// Go: ls/lsutil/userpreferences_generated.go:89 UserPreferences
// UserPreferences represents TypeScript language service preferences.
// PORT: Go embeds `CompletionPreferences` (the completion fields here) and
// names `InlayHintsPreferences` and `CodeLensUserPreferences` by type; here
// they are `inlay_hints` and `code_lens`. Some fields keep their names from
// before ts#64554 (`enable_auto_closing_tags` is Go `AutoClosingTags`,
// `enable_js_doc_completions` `CompleteJSDocs`, `enable_formatting`
// `FormatEnabled`, `enable_validation` `ValidateEnabled`,
// `use_aliases_for_rename` `ProvidePrefixAndSuffixTextForRename`).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct UserPreferences {
    pub format_code_settings: FormatCodeSettings,

    pub quote_preference: QuotePreference,
    pub lazy_configured_projects_from_external_project: Tristate, // !!!

    // A positive integer indicating the maximum length of a hover text before it is truncated.
    //
    // Default: `500`
    // PORT: Go `int` (64 bits).
    pub maximum_hover_length: i64, // !!!

    // ------- Completions -------

    // If enabled, TypeScript will search through all external modules' exports and add them to the completions list.
    // This affects lone identifier completions but not completions on the right hand side of `obj.`.
    pub include_completions_for_module_exports: Tristate,
    // Enables auto-import-style completions on partially-typed import statements. E.g., allows
    // `import write|` to be completed to `import { writeFile } from "fs"`.
    pub include_completions_for_import_statements: Tristate,
    // Unless this option is `false`,  member completion lists triggered with `.` will include entries
    // on potentially-null and potentially-undefined values, with insertion text to replace
    // preceding `.` tokens with `?.`.
    pub include_automatic_optional_chain_completions: Tristate,
    // If enabled, completions for class members (e.g. methods and properties) will include
    // a whole declaration for the member.
    // E.g., `class A { f| }` could be completed to `class A { foo(): number {} }`, instead of
    // `class A { foo }`.
    pub include_completions_with_class_member_snippets: Tristate,
    // If enabled, object literal methods will have a method declaration completion entry in addition
    // to the regular completion entry containing just the method name.
    // E.g., `const objectLiteral: T = { f| }` could be completed to `const objectLiteral: T = { foo(): void {} }`,
    // in addition to `const objectLiteral: T = { foo }`.
    pub include_completions_with_object_literal_method_snippets: Tristate,
    pub jsx_attribute_completion_style: JsxAttributeCompletionStyle,
    pub enable_auto_closing_tags: Tristate,
    pub enable_js_doc_completions: Tristate,
    pub generate_return_in_doc_template: Tristate,

    // ------- AutoImports --------
    pub import_module_specifier_preference: ImportModuleSpecifierPreference, // !!!
    // Determines whether we import `foo/index.ts` as "foo", "foo/index", or "foo/index.js"
    pub import_module_specifier_ending: ImportModuleSpecifierEndingPreference, // !!!
    pub auto_import_specifier_exclude_regexes: Option<Vec<String>>,            // !!!
    pub auto_import_file_exclude_patterns: Option<Vec<String>>,
    pub auto_import_entrypoint_directory_search: Tristate,
    pub prefer_type_only_auto_imports: Tristate,

    // ------- OrganizeImports -------

    // Indicates which deterministic preset should be used to sort imports.
    // "auto" detects the existing ordinal case sensitivity where possible.
    pub organize_imports_sort: OrganizeImportsSort, // !!!
    // Indicates whether imports should be organized in a case-insensitive manner.
    //
    // Default: TSUnknown ("auto" in strada), will perform detection
    pub organize_imports_ignore_case: Tristate, // !!!
    // Indicates whether imports should be organized via an "ordinal" (binary) comparison using the numeric value of their
    // code points, or via "unicode" natural sorting. This implementation is locale-agnostic and approximates the practical
    // import-sorting behavior rather than the full Unicode Collation Algorithm.
    //
    // Default: Ordinal
    pub organize_imports_collation: OrganizeImportsCollation, // !!!
    // Indicates the locale to use for "unicode" collation in legacy clients. This is accepted for compatibility, but
    // currently ignored because organize-import sorting is deterministic and locale-agnostic.
    //
    // This preference is ignored if organizeImportsCollation is not `unicode`.
    //
    // Default: `"en"`
    pub organize_imports_locale: String, // !!!
    // Indicates whether numeric collation should be used for digit sequences in strings. When `true`, will collate
    // strings such that `a1z < a2z < a100z`. When `false`, will collate strings such that `a1z < a100z < a2z`.
    //
    // This preference is ignored if organizeImportsCollation is not `unicode`.
    //
    // Default: `false`
    pub organize_imports_numeric_collation: Tristate, // !!!
    // Indicates whether accents and other diacritic marks are considered unequal for the purpose of sorting.
    //
    // This preference is ignored if organizeImportsCollation is not `unicode`.
    //
    // Default: `true`
    pub organize_imports_accent_collation: Tristate, // !!!
    // Indicates whether upper case or lower case should sort first.
    //
    // This permission is ignored if:
    //	- organizeImportsCollation is not `unicode`
    //	- organizeImportsIgnoreCase is `true`
    //	- organizeImportsIgnoreCase is `auto` and the auto-detected case sensitivity is case-insensitive.
    //
    // Default: `false`
    pub organize_imports_case_first: OrganizeImportsCaseFirst, // !!!
    // Indicates where named type-only imports should sort. "inline" sorts named imports without regard to if the import is type-only.
    //
    // Default: `auto`, which defaults to `last`
    pub organize_imports_type_order: OrganizeImportsTypeOrder, // !!!

    // ------- MoveToFile -------
    pub allow_text_changes_in_new_files: Tristate, // !!!

    // ------- Rename -------
    pub use_aliases_for_rename: Tristate,
    pub allow_rename_of_import_path: Tristate,

    // ------- CodeFixes/Refactors -------
    pub provide_refactor_not_applicable_reason: Tristate, // !!!

    // ------- InlayHints -------
    pub inlay_hints: InlayHintsPreferences,

    // ------- CodeLens -------
    pub code_lens: CodeLensUserPreferences,

    // ------- Definition -------
    pub prefer_go_to_source_definition: bool,

    // ------- Symbols -------
    pub exclude_library_symbols_in_nav_to: Tristate,
    pub workspace_symbols_scope: WorkspaceSymbolsScope,

    // ------- Misc -------
    pub enable_formatting: Tristate,
    pub enable_validation: Tristate,
    pub disable_suggestions: Tristate,             // !!!
    pub disable_line_text_in_references: Tristate, // !!!
    pub display_parts_for_js_doc: Tristate,        // !!!
    pub report_style_checks_as_warnings: Tristate,
    pub locale: String,

    // ------- ATA -------

    // DisableAutomaticTypeAcquisition is the deprecated setting from typescript.disableAutomaticTypeAcquisition.
    pub disable_automatic_type_acquisition: Tristate,
    // AutomaticTypeAcquisitionEnabled is the unified setting from tsserver.automaticTypeAcquisition.enabled under the js/ts section.
    // When set, it takes precedence over DisableAutomaticTypeAcquisition.
    pub automatic_type_acquisition_enabled: Tristate,
    // TODO: add tsserver.web.typeAcquisition.enabled under the js/ts section for the web variant when web support is implemented.

    // ------- Project Configuration -------

    // CustomConfigFileName specifies a custom config file name to use before defaulting to tsconfig.json/jsconfig.json.
    pub custom_config_file_name: String,
}

impl UserPreferences {
    // Go: ls/lsutil/userpreferences.go:196 (UserPreferences).IsATADisabled
    // IsATADisabled returns whether Automatic Type Acquisition is disabled based on user preferences.
    // It checks the unified setting (tsserver.automaticTypeAcquisition.enabled) first,
    // then falls back to the deprecated setting (disableAutomaticTypeAcquisition).
    pub fn is_ata_disabled(&self) -> bool {
        if !self.automatic_type_acquisition_enabled.is_unknown() {
            return !self.automatic_type_acquisition_enabled.is_true();
        }
        self.disable_automatic_type_acquisition.is_true()
    }
}

// Go: ls/lsutil/userpreferences_generated.go:77 InlayHintsPreferences
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct InlayHintsPreferences {
    pub include_inlay_parameter_name_hints: IncludeInlayParameterNameHints,
    pub include_inlay_parameter_name_hints_when_argument_matches_name: Tristate,
    pub include_inlay_function_parameter_type_hints: Tristate,
    pub include_inlay_variable_type_hints: Tristate,
    pub include_inlay_variable_type_hints_when_type_matches_name: Tristate,
    pub include_inlay_property_declaration_type_hints: Tristate,
    pub include_inlay_function_like_return_type_hints: Tristate,
    pub include_inlay_enum_member_value_hints: Tristate,
}

// Go: ls/lsutil/userpreferences_generated.go:14 CodeLensUserPreferences
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct CodeLensUserPreferences {
    pub references_code_lens_enabled: Tristate,
    pub implementations_code_lens_enabled: Tristate,
    pub references_code_lens_show_on_all_functions: Tristate,
    pub implementations_code_lens_show_on_interface_methods: Tristate,
    pub implementations_code_lens_show_on_all_class_methods: Tristate,
}

// --- Enum Types ---

// Go: ls/lsutil/userpreferences.go:40 QuotePreference
// PORT: a Go string type. The value is always one of the Go constants, so a
// `&'static str` holds it.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct QuotePreference(pub &'static str);

impl QuotePreference {
    pub const UNKNOWN: QuotePreference = QuotePreference("");
    pub const AUTO: QuotePreference = QuotePreference("auto");
    pub const DOUBLE: QuotePreference = QuotePreference("double");
    pub const SINGLE: QuotePreference = QuotePreference("single");
}

// Go: ls/lsutil/userpreferences.go:49 WorkspaceSymbolsScope
// PORT: a Go string type. Since ts#64554 it has a parser
// (`parsePreferenceWorkspaceSymbolsScope`), so the value is one of the
// constants or "" (an unknown value). It stays a `Cow`.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct WorkspaceSymbolsScope(pub Cow<'static, str>);

impl WorkspaceSymbolsScope {
    pub const ALL_OPEN_PROJECTS: WorkspaceSymbolsScope =
        WorkspaceSymbolsScope(Cow::Borrowed("allOpenProjects"));
    pub const CURRENT_PROJECT: WorkspaceSymbolsScope =
        WorkspaceSymbolsScope(Cow::Borrowed("currentProject"));
}

// Go: ls/lsutil/userpreferences.go:56 JsxAttributeCompletionStyle
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct JsxAttributeCompletionStyle(pub &'static str);

impl JsxAttributeCompletionStyle {
    pub const UNKNOWN: JsxAttributeCompletionStyle = JsxAttributeCompletionStyle("");
    pub const AUTO: JsxAttributeCompletionStyle = JsxAttributeCompletionStyle("auto");
    pub const BRACES: JsxAttributeCompletionStyle = JsxAttributeCompletionStyle("braces");
    pub const NONE: JsxAttributeCompletionStyle = JsxAttributeCompletionStyle("none");
}

// Go: ls/lsutil/userpreferences.go:65 IncludeInlayParameterNameHints
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct IncludeInlayParameterNameHints(pub &'static str);

impl IncludeInlayParameterNameHints {
    pub const NONE: IncludeInlayParameterNameHints = IncludeInlayParameterNameHints("");
    pub const ALL: IncludeInlayParameterNameHints = IncludeInlayParameterNameHints("all");
    pub const LITERALS: IncludeInlayParameterNameHints = IncludeInlayParameterNameHints("literals");
}

// Go: ls/lsutil/userpreferences.go:73 OrganizeImportsSort
go_enum!(OrganizeImportsSort, i32 {
    AUTO = 0;
    ORDINAL = 1;
    ORDINAL_IGNORE_CASE = 2;
    NATURAL = 3;
    NATURAL_IGNORE_CASE = 4;
});

// Go: ls/lsutil/userpreferences.go:83 OrganizeImportsCollation
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub struct OrganizeImportsCollation(pub bool);

impl OrganizeImportsCollation {
    pub const ORDINAL: OrganizeImportsCollation = OrganizeImportsCollation(false);
    pub const UNICODE: OrganizeImportsCollation = OrganizeImportsCollation(true);
}

// Go: ls/lsutil/userpreferences.go:90 OrganizeImportsCaseFirst
go_enum!(OrganizeImportsCaseFirst, i32 {
    FALSE = 0;
    LOWER = 1;
    UPPER = 2;
});

// Go: ls/lsutil/userpreferences.go:98 OrganizeImportsTypeOrder
go_enum!(OrganizeImportsTypeOrder, i32 {
    AUTO = 0;
    LAST = 1;
    INLINE = 2;
    FIRST = 3;
});

// Go: ls/lsutil/userpreferences.go:107 parsePreferenceTristate
pub(super) fn parse_preference_tristate(value: &LspAny) -> Tristate {
    if let LspAny::Bool(value) = value {
        return bool_to_tristate(*value);
    }
    Tristate::Unknown
}

// Go: ls/lsutil/userpreferences.go:114 parseOrganizeImportsCaseSensitivity
pub(super) fn parse_organize_imports_case_sensitivity(value: &LspAny) -> Tristate {
    if let LspAny::String(value) = value {
        match strings_to_lower(value).as_str() {
            "caseinsensitive" => return Tristate::True,
            "casesensitive" => return Tristate::False,
            _ => {}
        }
    }
    parse_preference_tristate(value)
}

// Go: ls/lsutil/userpreferences.go:126 invertPreferenceValue
fn invert_preference_value(value: LspAny) -> LspAny {
    if let LspAny::Bool(value) = value {
        return LspAny::Bool(!value);
    }
    value
}

// Go: ls/lsutil/userpreferences.go:133 getPreferenceConfigValue
// PORT: Go returns `(value, ok)`; `None` is `ok == false`. The value is a
// copy, inverted when its tag says `,invert`.
pub(super) fn get_preference_config_value(
    config: &IndexMap<String, LspAny>,
    paths: &[&str],
) -> Option<LspAny> {
    for tag in paths {
        let (path, modifier) = tag.split_once(',').unwrap_or((tag, ""));
        if let Some(value) = get_nested_value(config, path) {
            let value = value.clone();
            if modifier == "invert" {
                return Some(invert_preference_value(value));
            }
            return Some(value);
        }
    }
    None
}

// Go: ls/lsutil/userpreferences.go:147 getNestedValue
// PORT: Go returns `(value, ok)`; `None` is `ok == false`.
fn get_nested_value<'a>(config: &'a IndexMap<String, LspAny>, path: &str) -> Option<&'a LspAny> {
    let mut parts = path.split('.');
    let mut current = config.get(parts.next()?)?;
    for part in parts {
        let LspAny::Object(m) = current else {
            return None;
        };
        current = m.get(part)?;
    }
    Some(current)
}

/// Go `any` in the config map of `MarshalJSONTo`: the values it holds.
/// PORT: Go writes an `int` as an integer token; an `LspAny` number is an
/// `f64`, which would round one past 2^53.
#[derive(Clone, Debug, PartialEq)]
pub(super) enum ConfigValue {
    Bool(bool),
    Int(i64),
    String(String),
    Strings(Vec<String>),
    Map(IndexMap<String, ConfigValue>),
}

impl MarshalerTo for ConfigValue {
    fn marshal_json_to(&self, enc: &mut String) -> Result<(), JsonError> {
        match self {
            ConfigValue::Bool(v) => v.marshal_json_to(enc),
            ConfigValue::Int(v) => v.marshal_json_to(enc),
            ConfigValue::String(v) => v.marshal_json_to(enc),
            ConfigValue::Strings(v) => v.marshal_json_to(enc),
            ConfigValue::Map(v) => v.marshal_json_to(enc),
        }
    }
}

// Go: ls/lsutil/userpreferences.go:162 setNestedValue
pub(super) fn set_nested_value(
    config: &mut IndexMap<String, ConfigValue>,
    path: &str,
    value: ConfigValue,
) {
    let parts: Vec<&str> = path.split('.').collect();
    let mut current = config;
    for part in &parts[..parts.len() - 1] {
        if !matches!(current.get(*part), Some(ConfigValue::Map(_))) {
            current.insert(part.to_string(), ConfigValue::Map(IndexMap::new()));
        }
        let Some(ConfigValue::Map(next)) = current.get_mut(*part) else {
            unreachable!("the map was just set")
        };
        current = next;
    }
    current.insert(parts[parts.len() - 1].to_string(), value);
}

// Go: ls/lsutil/userpreferences.go:176 normalizeCustomConfigFileName
// PORT: Go `strings.TrimSpace` trims Unicode White_Space, as `str::trim`
// does.
pub(super) fn normalize_custom_config_file_name(name: &str) -> String {
    let name = name.trim();
    if name.contains(['/', '\\']) || name == ".." || name == "." {
        return String::new();
    }
    name.to_string()
}

// Go: ls/lsutil/userpreferences.go:184 (*UserPreferences).UnmarshalJSONFrom
impl UnmarshalerFrom for UserPreferences {
    fn unmarshal_json_from(&mut self, dec: &mut JsonDecoder<'_>) -> Result<(), JsonError> {
        let mut config: IndexMap<String, LspAny> = IndexMap::new();
        json_unmarshal_decode(dec, &mut config)?;
        // Start with defaults, then overlay parsed values
        *self = new_default_user_preferences().with_config(&config);
        Ok(())
    }
}

// --- Helper methods ---

impl UserPreferences {
    // Go: ls/lsutil/userpreferences.go:203 (UserPreferences).ModuleSpecifierPreferences
    pub fn module_specifier_preferences(&self) -> crate::modulespecifiers::UserPreferences {
        crate::modulespecifiers::UserPreferences {
            import_module_specifier_preference: self.import_module_specifier_preference,
            import_module_specifier_ending: self.import_module_specifier_ending,
            auto_import_specifier_exclude_regexes: self
                .auto_import_specifier_exclude_regexes
                .clone()
                .unwrap_or_default(),
        }
    }

    // Go: ls/lsutil/userpreferences.go:211 (UserPreferences).ParsedAutoImportFileExcludePatterns
    // PORT: Go returns a nil `*SpecMatcher` for no patterns; that is `None`.
    pub fn parsed_auto_import_file_exclude_patterns(
        &self,
        use_case_sensitive_file_names: bool,
    ) -> Option<crate::frontend::vfs::vfsmatch::SpecMatcher> {
        crate::frontend::vfs::vfsmatch::new_spec_matcher(
            self.auto_import_file_exclude_patterns
                .as_deref()
                .unwrap_or_default(),
            "",
            crate::frontend::vfs::vfsmatch::Usage::Exclude,
            use_case_sensitive_file_names,
        )
    }

    // Go: ls/lsutil/userpreferences.go:215 (UserPreferences).IsModuleSpecifierExcluded
    pub fn is_module_specifier_excluded(&self, module_specifier: &str) -> bool {
        crate::modulespecifiers::is_excluded_by_regex(
            module_specifier,
            self.auto_import_specifier_exclude_regexes
                .as_deref()
                .unwrap_or_default(),
        )
    }
}

// Go: ls/lsutil/userpreferences.go:219 ParseUserPreferences
pub fn parse_user_preferences(items: &IndexMap<String, LspAny>) -> UserPreferences {
    let mut prefs = new_default_user_preferences();
    // Apply editor settings first (tabSize, indentSize, etc.) as raw-name defaults,
    // then overlay language-specific settings with increasing precedence:
    // editor < javascript < typescript < js/ts
    if let Some(editor_item) = items.get("editor")
        && !matches!(editor_item, LspAny::Null)
        && let LspAny::Object(editor_settings) = editor_item
    {
        let mut normalized_settings = editor_settings.clone();
        if let Some(tab_size) = normalized_settings.get("tabSize").cloned()
            && !normalized_settings.contains_key("indentSize")
        {
            normalized_settings.insert("indentSize".to_string(), tab_size);
        }
        if let Some(insert_spaces) = normalized_settings.get("insertSpaces").cloned()
            && !normalized_settings.contains_key("convertTabsToSpaces")
        {
            normalized_settings.insert("convertTabsToSpaces".to_string(), insert_spaces);
        }
        let mut config: IndexMap<String, LspAny> = IndexMap::new();
        config.insert("unstable".to_string(), LspAny::Object(normalized_settings));
        prefs = prefs.with_config(&config);
    }
    // Apply javascript, then typescript, then js/ts (highest precedence).
    for section in ["javascript", "typescript", "js/ts"] {
        if let Some(item) = items.get(section)
            && !matches!(item, LspAny::Null)
            && let LspAny::Object(settings) = item
        {
            prefs = prefs.with_config(settings);
        }
    }
    prefs
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ls::lsutil::IndentStyle;

    fn config(entries: Vec<(&str, LspAny)>) -> IndexMap<String, LspAny> {
        entries
            .into_iter()
            .map(|(k, v)| (k.to_string(), v))
            .collect()
    }

    fn object(entries: Vec<(&str, LspAny)>) -> LspAny {
        LspAny::Object(config(entries))
    }

    fn string(s: &str) -> LspAny {
        LspAny::String(s.to_string())
    }

    // Go: ls/lsutil/userpreferences_test.go:13 TestUserPreferencesParsingEdgeCases (ts#64554)
    #[test]
    fn test_user_preferences_parsing_edge_cases() {
        type Expected = fn(&mut UserPreferences);
        let tests: Vec<(&str, IndexMap<String, LspAny>, Expected)> = vec![
            (
                "null raw values leave preferences unchanged",
                config(vec![
                    ("quotePreference", LspAny::Null),
                    ("maximumHoverLength", LspAny::Null),
                    ("includeCompletionsForModuleExports", LspAny::Null),
                ]),
                |_| {},
            ),
            (
                "invalid boolean becomes unknown",
                config(vec![(
                    "includeCompletionsForModuleExports",
                    string("invalid"),
                )]),
                |p| p.include_completions_for_module_exports = Tristate::Unknown,
            ),
            (
                "case-insensitive enums",
                config(vec![
                    ("quotePreference", string("SINGLE")),
                    ("jsxAttributeCompletionStyle", string("BRACES")),
                    ("organizeImportsCaseFirst", string("LOWER")),
                    ("includeInlayParameterNameHints", string("ALL")),
                    ("organizeImportsTypeOrder", string("FIRST")),
                    ("workspaceSymbolsScope", string("CURRENTPROJECT")),
                ]),
                |p| {
                    p.quote_preference = QuotePreference::SINGLE;
                    p.jsx_attribute_completion_style = JsxAttributeCompletionStyle::BRACES;
                    p.organize_imports_case_first = OrganizeImportsCaseFirst::LOWER;
                    p.inlay_hints.include_inlay_parameter_name_hints =
                        IncludeInlayParameterNameHints::ALL;
                    p.organize_imports_type_order = OrganizeImportsTypeOrder::FIRST;
                    p.workspace_symbols_scope = WorkspaceSymbolsScope::CURRENT_PROJECT;
                },
            ),
            (
                "present null primary path prevents fallback",
                config(vec![(
                    "suggest",
                    object(vec![
                        ("jsdoc", object(vec![("enabled", LspAny::Null)])),
                        ("completeJSDocs", LspAny::Bool(false)),
                    ]),
                )]),
                |_| {},
            ),
            (
                "null case sensitivity becomes unknown",
                config(vec![(
                    "preferences",
                    object(vec![(
                        "organizeImports",
                        object(vec![("caseSensitivity", LspAny::Null)]),
                    )]),
                )]),
                |p| p.organize_imports_ignore_case = Tristate::Unknown,
            ),
            (
                "numeric conversion and array filtering",
                config(vec![
                    ("maximumHoverLength", LspAny::Number(9.8)),
                    ("indentStyle", LspAny::Number(1.7)),
                    (
                        "autoImportFileExcludePatterns",
                        LspAny::Array(vec![
                            string("first"),
                            LspAny::Bool(false),
                            LspAny::Number(3.0),
                            string("second"),
                        ]),
                    ),
                ]),
                |p| {
                    p.maximum_hover_length = 9;
                    p.format_code_settings.editor_settings.indent_style = IndentStyle::BLOCK;
                    p.auto_import_file_exclude_patterns =
                        Some(vec!["first".to_string(), "second".to_string()]);
                },
            ),
            (
                "empty array is not nil",
                config(vec![(
                    "autoImportFileExcludePatterns",
                    LspAny::Array(vec![]),
                )]),
                |p| p.auto_import_file_exclude_patterns = Some(Vec::new()),
            ),
            (
                "invalid module specifier preference uses its default",
                config(vec![(
                    "importModuleSpecifierPreference",
                    LspAny::Bool(true),
                )]),
                |p| {
                    p.import_module_specifier_preference =
                        ImportModuleSpecifierPreference::Shortest;
                },
            ),
        ];
        for (name, config, expected) in tests {
            let mut base = new_default_user_preferences();
            base.quote_preference = QuotePreference::DOUBLE;
            base.maximum_hover_length = 17;
            base.organize_imports_ignore_case = Tristate::True;
            let mut want = base.clone();
            expected(&mut want);
            assert_eq!(base.with_config(&config), want, "{name}");
        }
    }

    /// Go `json.Unmarshal([]byte(text), &config)` with `config map[string]any`.
    fn json_config(text: &str) -> IndexMap<String, LspAny> {
        let mut config: IndexMap<String, LspAny> = IndexMap::new();
        crate::frontend::json::json_unmarshal(text.as_bytes(), &mut config, &[]).expect("json");
        config
    }

    // Go: ls/lsutil/userpreferences_test.go:254 TestUserPreferencesParseUnstable
    #[test]
    fn test_user_preferences_parse_unstable() {
        type Expected = fn(&mut UserPreferences);
        let tests: Vec<(&str, &str, Expected)> = vec![
            (
                "unstable fields with correct casing",
                r#"{"unstable": {"disableSuggestions": true, "maximumHoverLength": 100, "allowRenameOfImportPath": true}}"#,
                |p| {
                    p.disable_suggestions = Tristate::True;
                    p.maximum_hover_length = 100;
                    p.allow_rename_of_import_path = Tristate::True;
                },
            ),
            (
                "nested preferences path",
                r#"{"preferences": {"quoteStyle": "single", "useAliasesForRenames": true}}"#,
                |p| {
                    p.quote_preference = QuotePreference::SINGLE;
                    p.use_aliases_for_rename = Tristate::True;
                },
            ),
            (
                "suggest section",
                r#"{"suggest": {"autoImports": false, "includeCompletionsForImportStatements": true}}"#,
                |p| {
                    p.include_completions_for_module_exports = Tristate::False;
                    p.include_completions_for_import_statements = Tristate::True;
                },
            ),
            (
                "inlayHints with invert",
                r#"{"inlayHints": {"parameterNames": {"enabled": "all", "suppressWhenArgumentMatchesName": true}}}"#,
                |p| {
                    p.inlay_hints.include_inlay_parameter_name_hints =
                        IncludeInlayParameterNameHints::ALL;
                    p.inlay_hints
                        .include_inlay_parameter_name_hints_when_argument_matches_name =
                        Tristate::False; // inverted
                },
            ),
            (
                "mixed config",
                r#"{"unstable": {"displayPartsForJSDoc": true}, "preferences": {"importModuleSpecifier": "relative"}, "workspaceSymbols": {"excludeLibrarySymbols": true, "scope": "currentProject"}}"#,
                |p| {
                    p.display_parts_for_js_doc = Tristate::True;
                    p.import_module_specifier_preference =
                        ImportModuleSpecifierPreference::Relative;
                    p.exclude_library_symbols_in_nav_to = Tristate::True;
                    p.workspace_symbols_scope = WorkspaceSymbolsScope::CURRENT_PROJECT;
                },
            ),
            (
                "stable config overrides unstable",
                r#"{"unstable": {"quotePreference": "double"}, "preferences": {"quoteStyle": "single"}}"#,
                |p| p.quote_preference = QuotePreference::SINGLE, // stable wins
            ),
            (
                "unstable sets value when no stable config",
                r#"{"unstable": {"includeAutomaticOptionalChainCompletions": false}}"#,
                |p| p.include_automatic_optional_chain_completions = Tristate::False,
            ),
            (
                "any field can be passed via unstable by its raw name",
                r#"{"unstable": {"quotePreference": "double", "includeCompletionsForModuleExports": true, "excludeLibrarySymbolsInNavTo": true}}"#,
                |p| {
                    p.quote_preference = QuotePreference::DOUBLE;
                    p.include_completions_for_module_exports = Tristate::True;
                    p.exclude_library_symbols_in_nav_to = Tristate::True;
                },
            ),
            (
                "TypeScript raw names work in unstable section",
                r#"{"unstable": {"includeCompletionsForModuleExports": true, "quotePreference": "single", "providePrefixAndSuffixTextForRename": true, "includeInlayParameterNameHints": "all", "organizeImportsLocale": "en"}}"#,
                |p| {
                    p.include_completions_for_module_exports = Tristate::True;
                    p.quote_preference = QuotePreference::SINGLE;
                    p.use_aliases_for_rename = Tristate::True;
                    p.organize_imports_locale = "en".to_string();
                    p.inlay_hints.include_inlay_parameter_name_hints =
                        IncludeInlayParameterNameHints::ALL;
                },
            ),
            (
                "old raw organize imports unicode preferences load as raw state",
                r#"{"unstable": {"organizeImportsCollation": "unicode", "organizeImportsCaseFirst": "upper", "organizeImportsIgnoreCase": false, "organizeImportsNumericCollation": true}}"#,
                |p| {
                    p.organize_imports_collation = OrganizeImportsCollation::UNICODE;
                    p.organize_imports_case_first = OrganizeImportsCaseFirst::UPPER;
                    p.organize_imports_ignore_case = Tristate::False;
                    p.organize_imports_numeric_collation = Tristate::True;
                },
            ),
            (
                "old top-level raw organize imports unicode preferences load as raw state",
                r#"{"organizeImportsCollation": "unicode", "organizeImportsIgnoreCase": true}"#,
                |p| {
                    p.organize_imports_collation = OrganizeImportsCollation::UNICODE;
                    p.organize_imports_ignore_case = Tristate::True;
                },
            ),
            (
                "new top-level raw organize imports sort is accepted",
                r#"{"organizeImportsSort": "natural"}"#,
                |p| p.organize_imports_sort = OrganizeImportsSort::NATURAL,
            ),
            (
                "old raw organize imports ignore case loads as raw state",
                r#"{"unstable": {"organizeImportsIgnoreCase": true}}"#,
                |p| p.organize_imports_ignore_case = Tristate::True,
            ),
            (
                "new raw organize imports sort loads alongside old raw preferences",
                r#"{"unstable": {"organizeImportsSort": "ordinal", "organizeImportsCollation": "unicode", "organizeImportsIgnoreCase": true}}"#,
                |p| {
                    p.organize_imports_sort = OrganizeImportsSort::ORDINAL;
                    p.organize_imports_collation = OrganizeImportsCollation::UNICODE;
                    p.organize_imports_ignore_case = Tristate::True;
                },
            ),
            (
                "old nested organize imports unicode preferences load as raw state",
                r#"{"preferences": {"organizeImports": {"unicodeCollation": "unicode", "caseSensitivity": "caseSensitive", "numericCollation": true, "caseFirst": "upper"}}}"#,
                |p| {
                    p.organize_imports_collation = OrganizeImportsCollation::UNICODE;
                    p.organize_imports_ignore_case = Tristate::False;
                    p.organize_imports_numeric_collation = Tristate::True;
                    p.organize_imports_case_first = OrganizeImportsCaseFirst::UPPER;
                },
            ),
            (
                "new nested organize imports sort loads alongside old nested preferences",
                r#"{"preferences": {"organizeImports": {"sort": "ordinalIgnoreCase", "unicodeCollation": "unicode", "caseSensitivity": "caseSensitive"}}}"#,
                |p| {
                    p.organize_imports_sort = OrganizeImportsSort::ORDINAL_IGNORE_CASE;
                    p.organize_imports_collation = OrganizeImportsCollation::UNICODE;
                    p.organize_imports_ignore_case = Tristate::False;
                },
            ),
        ];
        for (name, json, expected) in tests {
            let mut want = UserPreferences::default();
            expected(&mut want);
            let parsed = UserPreferences::default().with_config(&json_config(json));
            assert_eq!(parsed, want, "{name}");
        }
    }

    // Lane B6 (the lsp prefs battery, lsp/prefs init hover): Go `int` is 64
    // bits, and a float64 out of its range converts to the minimum on amd64.
    #[test]
    fn test_user_preferences_go_int_width() {
        let parsed = UserPreferences::default().with_config(&json_config(
            r#"{"maximumHoverLength": 4294967306, "indentStyle": 4294967296}"#,
        ));
        assert_eq!(parsed.maximum_hover_length, 4_294_967_306);
        assert_eq!(
            parsed.format_code_settings.editor_settings.indent_style,
            IndentStyle(4_294_967_296)
        );
        let parsed = UserPreferences::default().with_config(&json_config(
            r#"{"unstable": {"maximumHoverLength": 1e19}, "format": {"indentStyle": -1e300}}"#,
        ));
        assert_eq!(parsed.maximum_hover_length, i64::MIN);
        assert_eq!(
            parsed.format_code_settings.editor_settings.indent_style,
            IndentStyle(i64::MIN)
        );
    }

    // Lane B6 round 2: `MarshalJSONTo` against Go's output at the pin (`json.Marshal`
    // of the same values, a scratch Go test in ls/lsutil): nil slices are omitted,
    // empty ones are `[]`, and an `int` is an integer token.
    #[test]
    fn test_user_preferences_marshal_matches_go() {
        let marshal =
            |p: &UserPreferences| crate::frontend::json::json_marshal(p, &[]).expect("marshal");
        let strings = |v: &[&str]| Some(v.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        let zero = UserPreferences::default();
        assert_eq!(marshal(&zero), ZERO);
        let empty = UserPreferences {
            auto_import_file_exclude_patterns: strings(&[]),
            auto_import_specifier_exclude_regexes: strings(&[]),
            ..UserPreferences::default()
        };
        assert_eq!(marshal(&empty), EMPTY_SLICES);
        let slices = UserPreferences {
            auto_import_file_exclude_patterns: strings(&["a"]),
            auto_import_specifier_exclude_regexes: strings(&["^b", "c"]),
            ..UserPreferences::default()
        };
        assert_eq!(marshal(&slices), SLICES);
        for (input, want) in ROUND_TRIPS {
            let mut parsed = UserPreferences::default();
            crate::frontend::json::json_unmarshal(input.as_bytes(), &mut parsed, &[])
                .expect("unmarshal");
            assert_eq!(marshal(&parsed), *want, "{input}");
        }
    }

    const ZERO: &str = r#"{"preferences":{"organizeImports":{"caseFirst":"default","sort":"auto","typeOrder":"auto","unicodeCollation":"ordinal"}},"unstable":{"preferGoToSourceDefinition":false}}"#;
    const EMPTY_SLICES: &str = r#"{"preferences":{"autoImportFileExcludePatterns":[],"autoImportSpecifierExcludeRegexes":[],"organizeImports":{"caseFirst":"default","sort":"auto","typeOrder":"auto","unicodeCollation":"ordinal"}},"unstable":{"preferGoToSourceDefinition":false}}"#;
    const SLICES: &str = r#"{"preferences":{"autoImportFileExcludePatterns":["a"],"autoImportSpecifierExcludeRegexes":["^b","c"],"organizeImports":{"caseFirst":"default","sort":"auto","typeOrder":"auto","unicodeCollation":"ordinal"}},"unstable":{"preferGoToSourceDefinition":false}}"#;
    const ROUND_TRIPS: &[(&str, &str)] = &[
        (
            r#"{"maximumHoverLength":1e19}"#,
            r#"{"autoClosingTags":{"enabled":true},"format":{"convertTabsToSpaces":true,"enabled":true,"indentSize":4,"indentStyle":2,"indentSwitchCase":true,"insertSpaceAfterCommaDelimiter":true,"insertSpaceAfterConstructor":false,"insertSpaceAfterFunctionKeywordForAnonymousFunctions":false,"insertSpaceAfterKeywordsInControlFlowStatements":true,"insertSpaceAfterOpeningAndBeforeClosingJsxExpressionBraces":false,"insertSpaceAfterOpeningAndBeforeClosingNonemptyBraces":true,"insertSpaceAfterOpeningAndBeforeClosingNonemptyBrackets":false,"insertSpaceAfterOpeningAndBeforeClosingNonemptyParenthesis":false,"insertSpaceAfterOpeningAndBeforeClosingTemplateStringBraces":false,"insertSpaceAfterSemicolonInForStatements":true,"insertSpaceBeforeAndAfterBinaryOperators":true,"insertSpaceBeforeFunctionParenthesis":false,"newLineCharacter":"\n","placeOpenBraceOnNewLineForControlBlocks":false,"placeOpenBraceOnNewLineForFunctions":false,"semicolons":"ignore","tabSize":4,"trimTrailingWhitespace":true},"preferences":{"organizeImports":{"caseFirst":"default","sort":"auto","typeOrder":"auto","unicodeCollation":"ordinal"}},"reportStyleChecksAsWarnings":true,"suggest":{"autoImports":true,"includeCompletionsForImportStatements":true,"jsdoc":{"enabled":true,"generateReturns":true}},"unstable":{"allowRenameOfImportPath":true,"disableLineTextInReferences":true,"displayPartsForJSDoc":true,"maximumHoverLength":-9223372036854775808,"preferGoToSourceDefinition":false,"provideRefactorNotApplicableReason":true},"validate":{"enabled":true},"workspaceSymbols":{"excludeLibrarySymbols":true,"scope":"allOpenProjects"}}"#,
        ),
        (
            r#"{"maximumHoverLength":4294967306,"indentStyle":4294967296,"tabSize":-4294967292}"#,
            r#"{"autoClosingTags":{"enabled":true},"format":{"convertTabsToSpaces":true,"enabled":true,"indentSize":4,"indentStyle":4294967296,"indentSwitchCase":true,"insertSpaceAfterCommaDelimiter":true,"insertSpaceAfterConstructor":false,"insertSpaceAfterFunctionKeywordForAnonymousFunctions":false,"insertSpaceAfterKeywordsInControlFlowStatements":true,"insertSpaceAfterOpeningAndBeforeClosingJsxExpressionBraces":false,"insertSpaceAfterOpeningAndBeforeClosingNonemptyBraces":true,"insertSpaceAfterOpeningAndBeforeClosingNonemptyBrackets":false,"insertSpaceAfterOpeningAndBeforeClosingNonemptyParenthesis":false,"insertSpaceAfterOpeningAndBeforeClosingTemplateStringBraces":false,"insertSpaceAfterSemicolonInForStatements":true,"insertSpaceBeforeAndAfterBinaryOperators":true,"insertSpaceBeforeFunctionParenthesis":false,"newLineCharacter":"\n","placeOpenBraceOnNewLineForControlBlocks":false,"placeOpenBraceOnNewLineForFunctions":false,"semicolons":"ignore","tabSize":-4294967292,"trimTrailingWhitespace":true},"preferences":{"organizeImports":{"caseFirst":"default","sort":"auto","typeOrder":"auto","unicodeCollation":"ordinal"}},"reportStyleChecksAsWarnings":true,"suggest":{"autoImports":true,"includeCompletionsForImportStatements":true,"jsdoc":{"enabled":true,"generateReturns":true}},"unstable":{"allowRenameOfImportPath":true,"disableLineTextInReferences":true,"displayPartsForJSDoc":true,"maximumHoverLength":4294967306,"preferGoToSourceDefinition":false,"provideRefactorNotApplicableReason":true},"validate":{"enabled":true},"workspaceSymbols":{"excludeLibrarySymbols":true,"scope":"allOpenProjects"}}"#,
        ),
        (
            r#"{"preferences":{"autoImportFileExcludePatterns":[1,false]}}"#,
            r#"{"autoClosingTags":{"enabled":true},"format":{"convertTabsToSpaces":true,"enabled":true,"indentSize":4,"indentStyle":2,"indentSwitchCase":true,"insertSpaceAfterCommaDelimiter":true,"insertSpaceAfterConstructor":false,"insertSpaceAfterFunctionKeywordForAnonymousFunctions":false,"insertSpaceAfterKeywordsInControlFlowStatements":true,"insertSpaceAfterOpeningAndBeforeClosingJsxExpressionBraces":false,"insertSpaceAfterOpeningAndBeforeClosingNonemptyBraces":true,"insertSpaceAfterOpeningAndBeforeClosingNonemptyBrackets":false,"insertSpaceAfterOpeningAndBeforeClosingNonemptyParenthesis":false,"insertSpaceAfterOpeningAndBeforeClosingTemplateStringBraces":false,"insertSpaceAfterSemicolonInForStatements":true,"insertSpaceBeforeAndAfterBinaryOperators":true,"insertSpaceBeforeFunctionParenthesis":false,"newLineCharacter":"\n","placeOpenBraceOnNewLineForControlBlocks":false,"placeOpenBraceOnNewLineForFunctions":false,"semicolons":"ignore","tabSize":4,"trimTrailingWhitespace":true},"preferences":{"autoImportFileExcludePatterns":[],"organizeImports":{"caseFirst":"default","sort":"auto","typeOrder":"auto","unicodeCollation":"ordinal"}},"reportStyleChecksAsWarnings":true,"suggest":{"autoImports":true,"includeCompletionsForImportStatements":true,"jsdoc":{"enabled":true,"generateReturns":true}},"unstable":{"allowRenameOfImportPath":true,"disableLineTextInReferences":true,"displayPartsForJSDoc":true,"preferGoToSourceDefinition":false,"provideRefactorNotApplicableReason":true},"validate":{"enabled":true},"workspaceSymbols":{"excludeLibrarySymbols":true,"scope":"allOpenProjects"}}"#,
        ),
    ];
}
