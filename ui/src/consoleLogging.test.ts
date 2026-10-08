/**
 * The browser console gets an error's class, never the error: a caught exception's message can
 * carry a URL, a response body or a stack. Every name a `catch (…)` clause binds, or the first
 * parameter of a rejection handler binds (`.catch(…)`, the second argument of `.then(…)`),
 * destructured names included, may appear in a direct `console.*(…)` call only as the sole
 * argument of `errorLabel(…)`. The rule follows the binding, whatever it is called. A rejection
 * handler passed by reference is refused, since its body cannot be checked here. Out of scope: a
 * caught value copied into another variable first, or handed to a helper that logs it.
 */
/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const SRC = dirname(fileURLToPath(import.meta.url));

/**
 * `file:line names` for every console call whose arguments use a caught value other than as
 * `errorLabel(x)`, and `file:line handler <text>` for a rejection handler passed by reference.
 */
export function unlabelledCatchLogs(fileName: string, source: string): string[] {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const found: string[] = [];
  const at = (n: ts.Node) => `${fileName}:${sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1}`;
  const isLabelled = (id: ts.Identifier) => {
    const call = id.parent;
    return ts.isCallExpression(call) && ts.isIdentifier(call.expression) && call.expression.text === "errorLabel" && call.arguments.length === 1 && call.arguments[0] === id;
  };
  const scanConsoleArgs = (call: ts.CallExpression, caught: ReadonlySet<string>) => {
    const names = new Set<string>();
    const visit = (n: ts.Node) => {
      if (ts.isIdentifier(n) && caught.has(n.text) && !isLabelled(n)) names.add(n.text);
      n.forEachChild(visit);
    };
    call.arguments.forEach(visit);
    if (names.size > 0) found.push(`${at(call)} ${[...names].join(",")}`);
  };
  const isConsoleCall = (n: ts.Node): n is ts.CallExpression =>
    ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression) && ts.isIdentifier(n.expression.expression) && n.expression.expression.text === "console";
  /** Every name a binding introduces: `e`, `{ message }`, `[first, ...rest]`. */
  const boundNames = (name: ts.BindingName, into: Set<string>): Set<string> => {
    if (ts.isIdentifier(name)) into.add(name.text);
    else for (const el of name.elements) if (!ts.isOmittedExpression(el)) boundNames(el.name, into);
    return into;
  };
  /** The rejection handler of `p.catch(h)` or `p.then(ok, h)`, if `n` is such a call. */
  const rejectionHandler = (n: ts.Node): ts.Expression | null => {
    if (!ts.isCallExpression(n) || !ts.isPropertyAccessExpression(n.expression)) return null;
    const method = n.expression.name.text;
    const h = method === "catch" ? n.arguments[0] : method === "then" ? n.arguments[1] : undefined;
    if (!h || h.kind === ts.SyntaxKind.NullKeyword || (ts.isIdentifier(h) && h.text === "undefined")) return null;
    return h;
  };
  const walk = (n: ts.Node, caught: ReadonlySet<string>) => {
    if (isConsoleCall(n) && caught.size > 0) scanConsoleArgs(n, caught);
    if (ts.isCatchClause(n) && n.variableDeclaration) {
      const inner = boundNames(n.variableDeclaration.name, new Set(caught));
      n.block.forEachChild((c) => walk(c, inner));
      return;
    }
    const handler = rejectionHandler(n);
    if (handler && ts.isCallExpression(n)) {
      if (ts.isArrowFunction(handler) || ts.isFunctionExpression(handler)) {
        const param = handler.parameters[0];
        const inner = param ? boundNames(param.name, new Set(caught)) : caught;
        for (const c of [n.expression, ...n.arguments]) if (c !== handler) walk(c, caught);
        walk(handler.body, inner);
        return;
      }
      found.push(`${at(handler)} handler ${handler.getText(sf)}`);
    }
    n.forEachChild((c) => walk(c, caught));
  };
  walk(sf, new Set());
  return found;
}

function productionSources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) productionSources(path, out);
    else if (/\.tsx?$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".d.ts")) out.push(path);
  }
  return out;
}

describe("console logging", () => {
  it("no caught value reaches the console except through errorLabel", () => {
    const found = productionSources(SRC).flatMap((path) => unlabelledCatchLogs(relative(SRC, path), readFileSync(path, "utf8")));
    expect(found).toEqual([]);
  });

  it("the check follows the catch binding, whatever it is called", () => {
    const rejected = {
      "a catch clause logging the error": `try { save(); } catch (err) { console.error("Blocking-mode recordAttempt failed:", err); }`,
      "a callback's own exception, with a template message": `try { onSuccess(); } catch (cbErr) { console.warn(\`\${TAG} onSuccess callback threw\`, cbErr); }`,
      "a promise callback": `load().catch((e) => console.warn(e));`,
      "a value derived from the error": `try { x(); } catch (oops) { console.log(\`failed: \${oops instanceof Error ? oops.message : ""}\`); }`,
      "a destructured binding": `try { x(); } catch ({ message }) { console.error(message); }`,
      "the rejection handler of then": `load().then(show, (e) => console.error("load failed", e));`,
      "a handler passed by reference": `load().catch(console.error);`,
    };
    for (const [what, source] of Object.entries(rejected)) expect(unlabelledCatchLogs("t.ts", source), what).toHaveLength(1);
    const accepted = `try { onSuccess(); } catch (cbErr) { console.warn(\`\${TAG} onSuccess callback threw\`, errorLabel(cbErr)); }
      load().catch((e) => console.warn("could not load", errorLabel(e)));
      load().then((e) => console.log(e), () => undefined);
      load().then(show, undefined).then(show, null);
      try { y(); } catch (e) { report(e); }`;
    expect(unlabelledCatchLogs("t.ts", accepted)).toEqual([]);
  });
});
