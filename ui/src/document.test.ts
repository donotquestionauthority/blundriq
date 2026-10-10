/**
 * The page the browser loads is `index.html`, not anything React renders: its title is what a tab
 * and a bookmark show, and its icon is the mark in `public/`. jsdom never loads the file, so the
 * tests read it.
 */
/// <reference types="node" />
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const html = readFileSync(join(ROOT, "index.html"), "utf8");

describe("the document", () => {
  it("is titled after the app, not the scaffold", () => {
    expect(html).toContain("<title>BlundrIQ</title>");
  });

  it("links the icon in public/, and that icon is the app's own mark", () => {
    expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/favicon.svg" />');
    const icon = readFileSync(join(ROOT, "public", "favicon.svg"), "utf8");
    expect(icon).toContain("<svg");
    expect(icon.toLowerCase()).not.toContain("vite");
  });
});
