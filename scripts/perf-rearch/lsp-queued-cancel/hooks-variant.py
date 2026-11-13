"""Adds the current cancel test hooks (test_hooks::CancelHooks: the handled-cancel count and the
"dequeued" / "async" pause points, the classification accessor) to an earlier lsp/server.rs of this lane,
so the current tests can run against rounds 1-3. Prints the patched file.

    python3 -I hooks-variant.py <old server.rs> <current server.rs> > server.rs
"""
import sys

old, new = (open(p).read() for p in sys.argv[1:3])


def rep(s, a, b):
    assert s.count(a) == 1, a[:80]
    return s.replace(a, b)


s = old
has_hooks = 'cancel_hooks: test_hooks::CancelHooks' in old  # round 3: only the module is new
if not has_hooks:
    s = rep(s, '        canceled_queued_requests: Mutex::new(FxHashSet::default()),\n',
            '        canceled_queued_requests: Mutex::new(FxHashSet::default()),\n'
            '        #[cfg(feature = "test-hooks")]\n        cancel_hooks: test_hooks::CancelHooks::default(),\n')
    s = rep(s, '    pub canceled_queued_requests: Mutex<FxHashSet<crate::jsonrpc::ID>>,\n',
            '    pub canceled_queued_requests: Mutex<FxHashSet<crate::jsonrpc::ID>>,\n'
            '    #[cfg(feature = "test-hooks")]\n    pub cancel_hooks: test_hooks::CancelHooks,\n')
    # The handled count: at the end of cancel_request.
    i = s.index('    pub fn cancel_request(&self, raw_id: &lsproto::IntegerOrString) {')
    j = s.index('\n    }\n', i)
    s = s[:j] + '\n        drop(pending_client_requests);\n        #[cfg(feature = "test-hooks")]\n        self.cancel_hooks.note_handled();' + s[j:]
    s = rep(s, '            QueuedRequest::Request(req) => Rc::new(req),\n',
            '            QueuedRequest::Request(req) => {\n                #[cfg(feature = "test-hooks")]\n'
            '                self.shared.cancel_hooks.pause("dequeued", &req.method);\n                Rc::new(req)\n            }\n')
    s = rep(s, '                if let Err(ls_error) = do_async_work() {',
            '                #[cfg(feature = "test-hooks")]\n                self.shared.cancel_hooks.pause("async", &req.method);\n'
            '                if let Err(ls_error) = do_async_work() {')
# Replace (or add) the test_hooks module with the current one (reading the old sync flag in rounds 1-2).
marker = '/// Tests (`test-hooks`): the queued-cancel state that'
if marker in s:
    s = s[:s.index(marker)]
hooks = new[new.index(marker):]
if 'go_dispatch: GoDispatchModel' not in old:
    hooks = hooks.replace('self.go_dispatch.sync_part.load(Ordering::SeqCst)', 'self.dispatch_in_sync_part.load(Ordering::SeqCst)')
if 'fn blocks_on_api_update' not in old:
    # Rounds 1-2 have no APIUpdate list: every message counts.
    hooks = hooks.replace('self.blocks_on_api_update(&lsproto::Method(method.to_string().into()))', '{ let _ = method; true }')
sys.stdout.write(s.rstrip('\n') + '\n\n' + hooks)
