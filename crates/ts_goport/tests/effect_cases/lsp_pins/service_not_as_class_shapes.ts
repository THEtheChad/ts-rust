// @effect-diagnostics serviceNotAsClass:warning
import { Context } from "effect"

interface ConfigService {}

// No arguments to the service call.
const NoArguments = Context.Service<ConfigService>()

// Several modifiers and type arguments.
export declare const Declared: unknown
export const WithShape = Context.Service<{ readonly port: number }>("WithShape")
