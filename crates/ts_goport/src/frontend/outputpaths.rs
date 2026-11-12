//! Port of Go `internal/outputpaths` (outputpaths.go, commonsourcedirectory.go).

use crate::frontend::prelude::*;

// Go: outputpaths/outputpaths.go:9 OutputPathsHost
pub trait OutputPathsHost {
    fn common_source_directory(&self) -> String;
    /// #4712: the file extensions of the configured content mappers.
    fn content_mapper_extensions(&self) -> Vec<String>;
    /// ts#64581: the output extension rewrites of the configured content
    /// mappers (Go `ContentMapperExtensionRewrites`).
    fn content_mapper_extension_rewrites(&self) -> Vec<ExtensionRewrite>;
    fn get_current_directory(&self) -> String;
    fn use_case_sensitive_file_names(&self) -> bool;
}

// Go: outputpaths/outputpaths.go:16 OutputPaths
#[derive(Clone, Debug, Default)]
pub struct OutputPaths {
    js_file_path: String,
    source_map_file_path: String,
    declaration_file_path: String,
    declaration_map_path: String,
    external_output_file_path: String,
}

impl OutputPaths {
    // Go: outputpaths/outputpaths.go:25 (*OutputPaths).DeclarationFilePath
    pub fn declaration_file_path(&self) -> &str {
        &self.declaration_file_path
    }

    // Go: outputpaths/outputpaths.go:30 (*OutputPaths).JsFilePath
    pub fn js_file_path(&self) -> &str {
        &self.js_file_path
    }

    // Go: outputpaths/outputpaths.go:34 (*OutputPaths).SourceMapFilePath
    pub fn source_map_file_path(&self) -> &str {
        &self.source_map_file_path
    }

    // Go: outputpaths/outputpaths.go:38 (*OutputPaths).DeclarationMapPath
    pub fn declaration_map_path(&self) -> &str {
        &self.declaration_map_path
    }

    // Go: outputpaths/outputpaths.go:42 (*OutputPaths).ExternalOutputFilePath (ts#64581)
    /// The runtime output of a content-mapped file whose extension a mapper
    /// rewrites. The mapper writes it, not the compiler.
    pub fn external_output_file_path(&self) -> &str {
        &self.external_output_file_path
    }
}

// Go: `*outputpaths.OutputPaths` implements `declarations.OutputPaths`
// (transformers/declarations/transform.go:28).
impl crate::declarations::OutputPaths for OutputPaths {
    fn declaration_file_path(&self) -> String {
        self.declaration_file_path.clone()
    }

    fn js_file_path(&self) -> String {
        self.js_file_path.clone()
    }
}

// Go: outputpaths/outputpaths.go:46 ForceEmitPaths
/// Output paths to compute even when the options turn that output off (#4699).
/// The API emit and the builder signature emit set them.
#[derive(Clone, Copy, Debug, Default)]
pub struct ForceEmitPaths {
    pub dts: bool,
    pub js: bool,
    pub declaration_map: bool,
}

/// The `*ast.SourceFile` fields that Go's emit decisions read:
/// `GetOutputPathsFor` here and `sourceFileMayBeEmitted`
/// (`compiler::source_file_may_be_emitted`).
// PORT: the program has two representations of a source file: the
// frontend's `ParsedSourceFile` and the emit side's arena `Node`, whose
// fields sit behind a file guard (`program::with_emit_source_file`). Each
// gives this view of its file, so one port of each Go function serves both.
#[derive(Clone, Copy, Debug)]
pub struct EmitSourceFile<'a> {
    /// Go `sourceFile.FileName()`.
    pub file_name: &'a str,
    /// Go `sourceFile.ScriptKind`: `ast.IsJsonSourceFile` and
    /// `ast.IsSourceFileJS` read it.
    pub script_kind: ScriptKind,
    /// Go `sourceFile.IsDeclarationFile`.
    pub is_declaration_file: bool,
    /// The file's SourceFile node, whose content mapper `content_mapper`
    /// reads (`Node::NIL` for none).
    pub root: Node,
}

impl EmitSourceFile<'_> {
    /// Go `ast.IsJsonSourceFile` of the file.
    #[inline]
    pub fn is_json(&self) -> bool {
        self.script_kind == ScriptKind::JSON
    }

    /// Go `ast.IsSourceFileJS` of the file.
    #[inline]
    pub fn is_js(&self) -> bool {
        matches!(self.script_kind, ScriptKind::JS | ScriptKind::JSX)
    }

    /// Go `sourceFile.ContentMapper()`: "" when no content mapper made the
    /// file (#4712).
    // PERF: read on demand, as Go reads it: with mappers registered it is a
    // lock and map lookup, which `sourceFileMayBeEmitted` makes only after
    // its JS and declaration-file returns.
    #[inline]
    pub fn content_mapper(&self) -> &'static str {
        source_file_content_mapper(self.root)
    }
}

impl<'a> From<&'a ParsedSourceFile> for EmitSourceFile<'a> {
    #[inline]
    fn from(file: &'a ParsedSourceFile) -> Self {
        EmitSourceFile {
            file_name: file.file_name(),
            script_kind: file.script_kind,
            is_declaration_file: file.is_declaration_file,
            root: file.root,
        }
    }
}

// Go: outputpaths/outputpaths.go:52 GetOutputPathsFor (ts#64581)
pub fn get_output_paths_for(
    source_file: &EmitSourceFile<'_>,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
    force: ForceEmitPaths,
) -> OutputPaths {
    let file_name = source_file.file_name;
    let content_mapper = source_file.content_mapper();
    let own_output_file_path = get_own_emit_output_file_path(
        file_name,
        options,
        host,
        get_output_extension(file_name, options.jsx),
    );
    let is_json_file = source_file.is_json();
    // If json file emits to the same location skip writing it, if emitDeclarationOnly skip writing it
    let is_json_emitted_to_same_location = is_json_file
        && compare_rooted_text(
            file_name,
            &own_output_file_path,
            host.use_case_sensitive_file_names(),
        ) == 0;
    let mut paths = OutputPaths::default();
    if !content_mapper.is_empty() {
        paths.external_output_file_path = get_external_output_file_name(file_name, options, host);
    }
    // #4699: `force.js`, `force.dts` and `force.declaration_map` (Go `ForceEmitPaths`).
    // #4712: a content-mapped file gets no JS output. ts#63936: it keeps its
    // declaration map.
    if content_mapper.is_empty()
        && (force.js || options.emit_declaration_only != Tristate::True)
        && !is_json_emitted_to_same_location
    {
        paths.js_file_path = own_output_file_path;
        if !source_file.is_json() {
            paths.source_map_file_path = get_source_map_file_path(&paths.js_file_path, options);
        }
    }
    if force.dts || options.get_emit_declarations() && !is_json_file {
        paths.declaration_file_path =
            get_declaration_emit_output_file_path(file_name, options, host);
        if options.get_are_declaration_maps_enabled()
            || force.declaration_map && options.declaration_map.is_true()
        {
            paths.declaration_map_path = format!("{}.map", paths.declaration_file_path);
        }
    }
    paths
}

// Go: outputpaths/outputpaths.go:78 ForEachEmittedFile (ts#64581)
/// Runs `action` on the output paths of each file of `source_files`, then on
/// the external output path of each file of `external_output_source_files`
/// that `source_files` does not have. Stops when `action` returns true.
// PORT: Go keys `seen` by the `*ast.SourceFile` pointer: `Rc::as_ptr`.
pub fn for_each_emitted_file(
    host: &dyn OutputPathsHost,
    options: &CompilerOptions,
    mut action: impl FnMut(&OutputPaths, Option<&Rc<ParsedSourceFile>>) -> bool,
    source_files: &[Rc<ParsedSourceFile>],
    external_output_source_files: &[Rc<ParsedSourceFile>],
    force_dts_emit: bool,
) -> bool {
    let mut seen: FxHashSet<*const ParsedSourceFile> =
        FxHashSet::with_capacity_and_hasher(source_files.len(), Default::default());
    for source_file in source_files {
        seen.insert(Rc::as_ptr(source_file));
        // #4699: Go `ForceEmitPaths{Dts: forceDtsEmit}`.
        if action(
            &get_output_paths_for(
                &EmitSourceFile::from(&**source_file),
                options,
                host,
                ForceEmitPaths {
                    dts: force_dts_emit,
                    ..ForceEmitPaths::default()
                },
            ),
            Some(source_file),
        ) {
            return true;
        }
    }
    for source_file in external_output_source_files {
        if seen.contains(&Rc::as_ptr(source_file)) {
            continue;
        }
        let paths = OutputPaths {
            external_output_file_path: get_external_output_file_name(
                source_file.file_name(),
                options,
                host,
            ),
            ..OutputPaths::default()
        };
        if action(&paths, Some(source_file)) {
            return true;
        }
    }
    false
}

// Go: outputpaths/outputpaths.go:105 GetOutputJSFileName
pub fn get_output_js_file_name(
    input_file_name: &str,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
) -> String {
    // #4712: a content-mapped file has no JS output.
    if options.emit_declaration_only.is_true() || is_content_mapped_file_name(input_file_name, host)
    {
        return String::new();
    }
    let output_file_name = get_output_js_file_name_worker(input_file_name, options, host);
    if !file_extension_is(&output_file_name, EXTENSION_JSON)
        || compare_rooted_text(
            input_file_name,
            &output_file_name,
            host.use_case_sensitive_file_names(),
        ) != 0
    {
        return output_file_name;
    }
    String::new()
}

// Go: outputpaths/outputpaths.go:118 isContentMappedFileName (#4712)
fn is_content_mapped_file_name(file_name: &str, host: &dyn OutputPathsHost) -> bool {
    !get_longest_extension_from_path(
        file_name,
        &host.content_mapper_extensions(),
        !host.use_case_sensitive_file_names(),
    )
    .is_empty()
}

// Go: outputpaths/outputpaths.go:122 GetOutputJSFileNameWorker
pub fn get_output_js_file_name_worker(
    input_file_name: &str,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
) -> String {
    change_extension(
        &get_output_path_without_changing_extension(input_file_name, &options.out_dir, host),
        get_output_extension(input_file_name, options.jsx),
    )
}

// Go: outputpaths/outputpaths.go:127 GetOutputDeclarationFileNameWorker
pub fn get_output_declaration_file_name_worker(
    input_file_name: &str,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
) -> String {
    let mut dir = options.declaration_dir.as_str();
    if dir.is_empty() {
        dir = options.out_dir.as_str();
    }
    // #4712: Go `ChangeToDeclarationExtension`, not `ChangeExtension`.
    change_to_declaration_extension(
        &get_output_path_without_changing_extension(input_file_name, dir, host),
        host,
    )
}

// Go: outputpaths/outputpaths.go:136 GetExternalOutputFileName (ts#64581)
/// The output path of a content-mapped file with its rewritten extension, or
/// "" when no mapper rewrites its extension.
pub fn get_external_output_file_name(
    input_file_name: &str,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
) -> String {
    let output_path =
        get_output_path_without_changing_extension(input_file_name, &options.out_dir, host);
    rewrite_file_extension(
        &output_path,
        &host.content_mapper_extension_rewrites(),
        host.use_case_sensitive_file_names(),
    )
    .unwrap_or_default()
}

// Go: outputpaths/outputpaths.go:145 GetOutputExtension
pub fn get_output_extension(file_name: &str, jsx: JsxEmit) -> &'static str {
    if file_extension_is(file_name, EXTENSION_JSON) {
        EXTENSION_JSON
    } else if jsx == JsxEmit::PRESERVE
        && file_extension_is_one_of(file_name, &[EXTENSION_JSX, EXTENSION_TSX])
    {
        EXTENSION_JSX
    } else if file_extension_is_one_of(file_name, &[EXTENSION_MTS, EXTENSION_MJS]) {
        EXTENSION_MJS
    } else if file_extension_is_one_of(file_name, &[EXTENSION_CTS, EXTENSION_CJS]) {
        EXTENSION_CJS
    } else {
        EXTENSION_JS
    }
}

// Go: outputpaths/outputpaths.go:131 GetDeclarationEmitOutputFilePath (at 673a5f17d713;
// ts#64159 renames it getDeclarationEmitOutputFilePathForFileName, outputpaths/outputpaths.go:164)
pub fn get_declaration_emit_output_file_path(
    file: &str,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
) -> String {
    let output_dir: Option<&str> = if !options.declaration_dir.is_empty() {
        Some(&options.declaration_dir)
    } else if !options.out_dir.is_empty() {
        Some(&options.out_dir)
    } else {
        None
    };

    let path = if let Some(output_dir) = output_dir {
        get_source_file_path_in_new_dir_worker(
            file,
            output_dir,
            &host.get_current_directory(),
            &host.common_source_directory(),
            host.use_case_sensitive_file_names(),
        )
    } else {
        file.to_string()
    };
    // #4712
    change_to_declaration_extension(&path, host)
}

// Go: outputpaths/outputpaths.go:186 ChangeToDeclarationExtension (#4712, ts#64581)
/// A path whose extension a content mapper rewrites gets the declaration
/// extension of its new extension. Another content mapper extension `.ext`
/// becomes `.d.ext.ts`. Other paths get their declaration extension (Go
/// `GetDeclarationEmitExtensionForPath`).
pub fn change_to_declaration_extension(path: &str, host: &dyn OutputPathsHost) -> String {
    if let Some(rewritten) = rewrite_file_extension(
        path,
        &host.content_mapper_extension_rewrites(),
        host.use_case_sensitive_file_names(),
    ) {
        return change_to_declaration_extension_unexported(&rewritten);
    }
    let extension = get_longest_extension_from_path(path, &host.content_mapper_extensions(), false);
    if !extension.is_empty() {
        return format!("{}.d{}.ts", remove_extension(path, &extension), extension);
    }
    change_to_declaration_extension_unexported(path)
}

// Go: outputpaths/outputpaths.go:196 changeToDeclarationExtension (ts#64581)
// PORT: Go's private `changeToDeclarationExtension` has the snake name of
// the exported one, so it gets the `_unexported` suffix.
fn change_to_declaration_extension_unexported(path: &str) -> String {
    let mut path_without_extension = remove_file_extension(path);
    if path_without_extension == path {
        let extension = get_any_extension_from_path(path, &[], false);
        if !extension.is_empty() {
            path_without_extension = remove_extension(path, &extension);
        }
    }
    format!(
        "{}{}",
        path_without_extension,
        get_declaration_emit_extension_for_path(path)
    )
}

// Go: outputpaths/outputpaths.go:221 GetSourceFileNameInNewDir (ts#64159
// renamed GetSourceFilePathInNewDir; the port keeps that name)
// tsgo#4900: the same as the worker.
pub fn get_source_file_path_in_new_dir(
    file_name: &str,
    new_dir_path: &str,
    current_directory: &str,
    common_source_directory: &str,
    use_case_sensitive_file_names: bool,
) -> String {
    get_source_file_path_in_new_dir_worker(
        file_name,
        new_dir_path,
        current_directory,
        common_source_directory,
        use_case_sensitive_file_names,
    )
}

// Go: outputpaths/outputpaths.go:206 getOutputFileNameWithoutChangingExtension (ts#64159)
// A file whose root differs from the common source directory's keeps its
// name (Go `RelativePathFromDirectory` is not ok).
fn get_output_path_without_changing_extension(
    input_file_name: &str,
    output_directory: &str,
    host: &dyn OutputPathsHost,
) -> String {
    if !output_directory.is_empty() {
        let Some(relative_path) = relative_path_from_directory(
            &host.common_source_directory(),
            input_file_name,
            host.use_case_sensitive_file_names(),
        ) else {
            return input_file_name.to_string();
        };
        // Go: outputDirectory.ResolveRelativeFile(relativePath)
        return resolve_path(output_directory, &[&relative_path]);
    }
    input_file_name.to_string()
}

// Go: outputpaths/outputpaths.go:221 GetSourceFileNameInNewDir (ts#64159
// merged GetSourceFilePathInNewDirWorker into it; the port keeps the worker)
// tsgo#4900: `TrimFilePathPrefix` (in `relative_path_within_directory`) cuts
// the common source directory by runes, not by its byte length.
pub fn get_source_file_path_in_new_dir_worker(
    file_name: &str,
    new_dir_path: &str,
    current_directory: &str,
    common_source_directory: &str,
    use_case_sensitive_file_names: bool,
) -> String {
    let source_file_path = get_normalized_absolute_path(file_name, current_directory);
    // ts#64159 (outputpaths.go:222): a file name equal to the common source
    // directory stays as it is.
    if source_file_path == common_source_directory {
        return source_file_path;
    }
    // ts#64159 (outputpaths.go:225): `RelativeFilePathFromDirectory`. A file
    // that is not under the common source directory keeps its name. The
    // directory can end in a separator or not.
    match relative_path_within_directory(
        common_source_directory,
        &source_file_path,
        use_case_sensitive_file_names,
    ) {
        Some(relative) => combine_paths(new_dir_path, &[&relative]),
        None => source_file_path,
    }
}

// Go: outputpaths/outputpaths.go:183 getOwnEmitOutputFilePath (at 673a5f17d713;
// ts#64159 renames it getOwnEmitOutputFilePathForFileName, outputpaths/outputpaths.go:231)
fn get_own_emit_output_file_path(
    file_name: &str,
    options: &CompilerOptions,
    host: &dyn OutputPathsHost,
    extension: &str,
) -> String {
    let emit_output_file_path_without_extension = if !options.out_dir.is_empty() {
        let current_directory = host.get_current_directory();
        remove_file_extension(&get_source_file_path_in_new_dir(
            file_name,
            &options.out_dir,
            &current_directory,
            &host.common_source_directory(),
            host.use_case_sensitive_file_names(),
        ))
        .to_string()
    } else {
        remove_file_extension(file_name).to_string()
    };
    emit_output_file_path_without_extension + extension
}

// Go: outputpaths/outputpaths.go:246 GetSourceMapFilePath
pub fn get_source_map_file_path(js_file_path: &str, options: &CompilerOptions) -> String {
    if options.source_map.is_true() && !options.inline_source_map.is_true() {
        return format!("{js_file_path}.map");
    }
    String::new()
}

// Go: outputpaths/outputpaths.go:253 GetBuildInfoFileName
// ts#64159: the output name comes from the config file name, and the
// extension goes last, so an empty or dot stem ("c:/src/p/.json") keeps its
// directory. A config file on another root than rootDir keeps its own name
// (R4).
pub fn get_build_info_file_name(options: &CompilerOptions, opts: &ComparePathsOptions) -> String {
    if !options.is_incremental() && !options.build.is_true() {
        return String::new();
    }
    if !options.ts_build_info_file.is_empty() {
        return options.ts_build_info_file.clone();
    }
    if options.config_file_path.is_empty() {
        return String::new();
    }
    let config_file_name = options.config_file_path.as_str();
    let build_info_file_name = if !options.out_dir.is_empty() {
        if !options.root_dir.is_empty() {
            match relative_path_from_directory(
                &options.root_dir,
                config_file_name,
                opts.use_case_sensitive_file_names,
            ) {
                // Go: options.OutDir.ResolveRelativeFile(relativePath)
                Some(relative_path) => resolve_path(&options.out_dir, &[&relative_path]),
                None => config_file_name.to_string(),
            }
        } else {
            // Go: options.OutDir.ResolveFile(configFileName.BaseName())
            combine_paths(&options.out_dir, &[&get_base_file_name(config_file_name)])
        }
    } else {
        config_file_name.to_string()
    };
    remove_file_extension(&build_info_file_name).to_string() + EXTENSION_TS_BUILD_INFO
}

// Go: outputpaths/commonsourcedirectory.go:8 computeCommonSourceDirectoryOfFilenames
fn compute_common_source_directory_of_filenames(
    file_names: &[String],
    current_directory: &str,
    use_case_sensitive_file_names: bool,
) -> String {
    if file_names.is_empty() {
        // Can happen when all input files are .d.ts files.
        return current_directory.to_string();
    }
    common_directory_of_files(file_names, use_case_sensitive_file_names)
}

// Go: outputpaths/commonsourcedirectory.go:16 GetComputedCommonSourceDirectory
pub fn get_computed_common_source_directory(
    emitted_files: &[String],
    current_directory: &str,
    use_case_sensitive_file_names: bool,
) -> String {
    let mut common_source_directory = compute_common_source_directory_of_filenames(
        emitted_files,
        current_directory,
        use_case_sensitive_file_names,
    );
    if !common_source_directory.is_empty() {
        common_source_directory = ensure_trailing_directory_separator(&common_source_directory);
    }
    common_source_directory
}

// Go: outputpaths/commonsourcedirectory.go:20 GetCommonSourceDirectory
// PORT: Go `checkSourceFilesBelongToPath` is a nillable callback; its
// result is unused.
pub fn get_common_source_directory(
    options: &CompilerOptions,
    files: impl FnOnce() -> Vec<String>,
    current_directory: &str,
    use_case_sensitive_file_names: bool,
    check_source_files_belong_to_path: Option<&mut dyn FnMut(&[String], &str) -> bool>,
) -> String {
    let mut common_source_directory;
    if !options.root_dir.is_empty() {
        // If a rootDir is specified use it as the commonSourceDirectory
        common_source_directory = options.root_dir.clone();
        if let Some(check) = check_source_files_belong_to_path {
            check(&files(), &options.root_dir);
        }
    } else if !options.config_file_path.is_empty() {
        // If the rootDir is not specified, then the common source directory is the directory of the config file.
        common_source_directory = get_directory_path(&options.config_file_path);
        if let Some(check) = check_source_files_belong_to_path {
            check(&files(), &common_source_directory);
        }
    } else {
        common_source_directory = compute_common_source_directory_of_filenames(
            &files(),
            current_directory,
            use_case_sensitive_file_names,
        );
    }

    if !common_source_directory.is_empty() {
        // Make sure directory path ends with directory separator so this string can directly
        // used to replace with "" to get the relative path of the source file and the relative path doesn't
        // start with / making it rooted path
        common_source_directory = ensure_trailing_directory_separator(&common_source_directory);
    }

    common_source_directory
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A case-insensitive host whose common source directory is
    /// `common_source_directory`.
    struct InsensitiveHost {
        common_source_directory: &'static str,
    }

    impl OutputPathsHost for InsensitiveHost {
        fn common_source_directory(&self) -> String {
            self.common_source_directory.to_string()
        }
        fn content_mapper_extensions(&self) -> Vec<String> {
            Vec::new()
        }
        fn content_mapper_extension_rewrites(&self) -> Vec<ExtensionRewrite> {
            Vec::new()
        }
        fn get_current_directory(&self) -> String {
            "/".to_string()
        }
        fn use_case_sensitive_file_names(&self) -> bool {
            false
        }
    }

    const ROOT: &str = "^/~ts-uri~/mem/auth";

    // Go `getOutputFileNameWithoutChangingExtension` takes the path below the
    // common directory with `CaseSensitivity.RelativePathFromDirectory`,
    // which keeps the case of an encoded dynamic name. Go's answer (lane B2,
    // API probe `c_rootcase` with the oracle at the pin): rootDir
    // `<root>/a`, outDir `<root>/out`, file `<root>/A/x.ts` emits
    // `<root>/A/x.js`, not `<root>/out/x.js`.
    #[test]
    fn output_name_of_dynamic_name_keeps_case_below_common_directory() {
        let host = InsensitiveHost {
            common_source_directory: "^/~ts-uri~/mem/auth/a/",
        };
        let options = CompilerOptions {
            out_dir: format!("{ROOT}/out"),
            ..Default::default()
        };
        assert_eq!(
            get_output_js_file_name_worker(&format!("{ROOT}/A/x.ts"), &options, &host),
            format!("{ROOT}/A/x.js")
        );
        // A disk name folds case.
        let host = InsensitiveHost {
            common_source_directory: "/p/a/",
        };
        let options = CompilerOptions {
            out_dir: "/p/out".to_string(),
            ..Default::default()
        };
        assert_eq!(
            get_output_js_file_name_worker("/p/A/x.ts", &options, &host),
            "/p/out/x.js"
        );
    }

    // Go `GetOutputPathsFor` and `GetOutputJSFileName` compare the JSON
    // output with its input by `CaseSensitivity.CompareFilePaths`: a dynamic
    // name keeps its case, so outDir `<root>/SRC` is not the input directory
    // `<root>/src`. Go's answer (API case `json_dynamic_out_dir_case`):
    // `<root>/SRC/data.json` is emitted.
    #[test]
    fn json_output_of_dynamic_name_compares_with_case() {
        let host = InsensitiveHost {
            common_source_directory: "^/~ts-uri~/mem/auth/src/",
        };
        let options = CompilerOptions {
            out_dir: format!("{ROOT}/SRC"),
            ..Default::default()
        };
        let input = format!("{ROOT}/src/data.json");
        let output = format!("{ROOT}/SRC/data.json");
        assert_eq!(get_output_js_file_name(&input, &options, &host), output);
        let paths = get_output_paths_for(
            &EmitSourceFile {
                file_name: &input,
                script_kind: ScriptKind::JSON,
                is_declaration_file: false,
                root: Node::NIL,
            },
            &options,
            &host,
            ForceEmitPaths::default(),
        );
        assert_eq!(paths.js_file_path(), output);
        // A disk name folds case: the output would be its own input.
        let host = InsensitiveHost {
            common_source_directory: "/p/src/",
        };
        let options = CompilerOptions {
            out_dir: "/p/SRC".to_string(),
            ..Default::default()
        };
        assert_eq!(
            get_output_js_file_name("/p/src/data.json", &options, &host),
            ""
        );
    }
}
