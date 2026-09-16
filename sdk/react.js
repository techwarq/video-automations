// @clep/sdk/react — <Clep> wrapper (plain JS, zero build step).
// Usage:
//   import { Clep } from "@clep/sdk/react";
//   <Clep name="ai-research"><ResearchButton /></Clep>   // (JSX compiles to this)
//
// Or without JSX:
//   Clep({ name: "ai-research", children: React.createElement(ResearchButton) })

import React from "react";

export function Clep(props) {
  const { name, state, action, as: Tag = "div", style, children, ...rest } = props;
  return React.createElement(
    Tag,
    {
      "data-clep": name,
      ...(state ? { "data-clep-state": state } : {}),
      ...(action ? { "data-clep-action": action } : {}),
      style: { display: "contents", ...(style || {}) },
      ...rest,
    },
    children
  );
}

export function ClepSection(props) {
  const { name, title, children } = props;
  return React.createElement(
    "section",
    {
      "data-clep": name,
      "data-clep-title": title || name,
      style: { display: "contents" },
    },
    children
  );
}

export default Clep;
