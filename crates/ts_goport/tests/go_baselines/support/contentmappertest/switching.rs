//! Not a Go port (perf/first-principles A1): a mapper whose output follows
//! markers in the file, for the language server's synthetic reuse tests.
//! The output is the input text, with the extension `.mts` when the text
//! has `@mts`, `.cts` when it has `@cts`, else `.ts`. A text with `@fail`
//! fails its transform.

use super::prelude::*;

pub(super) struct SwitchingHandler;

impl MapperHandler for SwitchingHandler {
    fn handle_request(&self, _ctx: &Context, method: &str, params: JsonValue) -> HandlerResult {
        match method {
            contentmapper::METHOD_INITIALIZE => reply(initialize_result("mapper")),
            contentmapper::METHOD_TRANSFORM => {
                let p: TransformParams = unmarshal_params(&params)?;
                if p.content.contains("@fail") {
                    return Err(errors::new("switching mapper failed to transform the file"));
                }
                let mut mapped_output = identity_mapped_output(&p.content)?;
                if p.content.contains("@mts") {
                    mapped_output.extension = ".mts".to_string();
                } else if p.content.contains("@cts") {
                    mapped_output.extension = ".cts".to_string();
                }
                reply(TransformResult {
                    mapped_output,
                    ..TransformResult::default()
                })
            }
            _ => Err(unexpected_method(method)),
        }
    }
}
