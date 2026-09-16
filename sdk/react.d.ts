import type * as React from "react";

export interface ClepProps {
  name: string;
  state?: string;
  action?: string;
  as?: any;
  style?: React.CSSProperties;
  children?: React.ReactNode;
  [k: string]: any;
}

export declare function Clep(props: ClepProps): React.ReactElement;
export declare function ClepSection(props: {
  name: string;
  title?: string;
  children?: React.ReactNode;
}): React.ReactElement;
export default Clep;
