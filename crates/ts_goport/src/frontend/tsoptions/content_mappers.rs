//! Go: tsoptions/contentmappers.go (tsgo#4712).

use crate::contentmapper::{Manifest, is_valid_extension};
use crate::frontend::prelude::*;
use std::collections::BTreeMap;

// Go: tsoptions/contentmappers.go:22 contentMapperResolutionHost (ts#64581)
struct ContentMapperResolutionHost {
    fs: Rc<dyn Fs>,
    current_directory: String,
}

impl ResolutionHost for ContentMapperResolutionHost {
    // Go: tsoptions/contentmappers.go:27 (*contentMapperResolutionHost).FS
    fn fs(&self) -> &dyn Fs {
        &*self.fs
    }

    // Go: tsoptions/contentmappers.go:31 (*contentMapperResolutionHost).GetCurrentDirectory
    fn get_current_directory(&self) -> &str {
        &self.current_directory
    }
}

// Go: tsoptions/contentmappers.go:35 resolveContentMapperManifest (ts#64581)
// resolveContentMapperManifest locates packageName in node_modules (walking up from the directory of
// containingFile via node module resolution) and reads its package.json to produce the mapper's manifest
// and package directory. It never executes the package. On failure it returns a diagnostic describing why
// the mapper could not be resolved; on success the diagnostic is nil.
// PORT: Go nil `*ast.Diagnostic` is `None`. Go takes the file system; this
// takes the `ParseConfigHost` and uses its `FS()`.
pub fn resolve_content_mapper_manifest(
    host: &dyn ParseConfigHost,
    containing_file: &str,
    package_name: &str,
) -> (Manifest, String, Option<Diagnostic>) {
    let resolver_host: Rc<dyn ResolutionHost> = Rc::new(ContentMapperResolutionHost {
        fs: host.fs(),
        current_directory: get_directory_path(containing_file),
    });
    let resolver = new_resolver(ResolverOptions {
        host: Some(resolver_host),
        compiler_options: Some(Rc::new(CompilerOptions {
            module_resolution: ModuleResolutionKind::BUNDLER,
            ..Default::default()
        })),
        ..Default::default()
    });
    let resolved = resolver.resolve_package_directory(
        package_name,
        containing_file,
        RESOLUTION_MODE_NONE,
        None,
    );
    let Some(resolved) = resolved.filter(|resolved| !resolved.resolved_file_name.is_empty()) else {
        return (
            Manifest::default(),
            String::new(),
            Some(new_compiler_diagnostic(
                diag::The_content_mapper_package_0_could_not_be_resolved,
                args![package_name],
            )),
        );
    };
    let package_directory = resolved.resolved_file_name;

    let package_json_path = combine_paths(&package_directory, &["package.json"]);
    let (contents, ok) = host.fs().read_file(&package_json_path);
    if !ok {
        return (
            Manifest::default(),
            package_directory,
            Some(new_compiler_diagnostic(
                diag::The_content_mapper_package_0_could_not_be_resolved,
                args![package_name],
            )),
        );
    }
    // PORT: `contents` is the port form of the Go text; Go parses its bytes.
    let Ok(fields) = crate::frontend::packagejson::parse(&go_string_bytes(&contents)) else {
        return (
            Manifest::default(),
            package_directory,
            Some(new_compiler_diagnostic(
                diag::The_package_json_of_the_content_mapper_package_0_could_not_be_parsed,
                args![package_name],
            )),
        );
    };
    let (name, _) = fields.header_fields.name.get_value();
    if name.is_empty() {
        return (
            Manifest::default(),
            package_directory,
            Some(new_compiler_diagnostic(
                diag::The_package_json_of_the_content_mapper_package_0_does_not_specify_a_name,
                args![package_name],
            )),
        );
    }
    let (version, _) = fields.header_fields.version.get_value();

    // A content mapper package must declare how to run it: a "typescript.contentMapper" object with a non-empty
    // "exec" array of strings.
    let (cm, ok) = fields.content_mapper.get_value();
    if !ok {
        return (
            Manifest::default(),
            package_directory,
            Some(new_compiler_diagnostic(
                diag::The_package_json_of_the_content_mapper_package_0_does_not_declare_a_typescript_contentMapper_object,
                args![package_name],
            )),
        );
    }
    let (exec, ok) = cm.exec.get_value();
    if !ok || exec.is_empty() {
        return (
            Manifest::default(),
            package_directory,
            Some(new_compiler_diagnostic(
                diag::The_typescript_contentMapper_exec_of_the_content_mapper_package_0_must_be_a_non_empty_array_of_strings,
                args![package_name],
            )),
        );
    }
    let (compiler_options, _) = cm.compiler_options.get_value();
    let (dynamic_config, _) = cm.dynamic_config.get_value();
    let (output_extensions, valid) = cm.output_extensions.get_value();
    if cm.output_extensions.is_present() && !valid {
        return (
            Manifest::default(),
            package_directory,
            Some(new_compiler_diagnostic(
                diag::The_typescript_contentMapper_outputExtensions_of_the_content_mapper_package_0_must_be_an_object_with_string_values,
                args![package_name],
            )),
        );
    }
    // PORT: Go reads the keys in sorted order (`slices.Sorted(maps.Keys(...))`),
    // which is the `BTreeMap` order. A valid value is a non-nil Go map.
    let output_extensions: Option<BTreeMap<String, String>> =
        valid.then(|| output_extensions.into_iter().collect());
    for (source, output) in output_extensions.iter().flatten() {
        for extension in [source, output] {
            if extension.contains(['/', '\\']) {
                return (
                    Manifest::default(),
                    package_directory,
                    Some(new_compiler_diagnostic(
                        diag::Content_mapper_extension_0_must_not_contain_path_separators,
                        args![extension],
                    )),
                );
            }
        }
        if !is_valid_extension(source) || !is_valid_extension(output) {
            return (
                Manifest::default(),
                package_directory,
                Some(new_compiler_diagnostic(
                    diag::The_typescript_contentMapper_outputExtensions_of_the_content_mapper_package_0_contains_an_invalid_mapping_from_1_to_2_Extensions_must_be_non_empty_and_begin_with_a,
                    args![package_name, source, output],
                )),
            );
        }
    }
    (
        Manifest {
            name,
            version,
            exec,
            compiler_options,
            dynamic_config,
            default_output_extensions: output_extensions,
        },
        package_directory,
        None,
    )
}
