/**
 * Internal types for the framework-next extractor.
 * These are used during extraction before envelope construction.
 */

export type RouterType = "app" | "pages";

/** A discovered Next.js page/screen */
export interface ExtractedScreen {
  routerType: RouterType;
  /** Normalized URL pattern, e.g. "/billing/[invoiceId]" */
  urlPattern: string;
  /** Repo-relative file path */
  filePath: string;
  /** Dynamic segment names extracted from path */
  dynamicSegments: string[];
  /** Whether this is a static generated page (has getStaticProps or generateStaticParams) */
  isSSG: boolean;
  /** Whether this is a server-rendered page (has getServerSideProps or default in App Router) */
  isSSR: boolean;
}

/** A discovered Next.js API route handler */
export interface ExtractedAPIRoute {
  routerType: RouterType;
  /** Normalized URL pattern */
  urlPattern: string;
  /** Repo-relative file path */
  filePath: string;
  /** HTTP methods exported (GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS) */
  httpMethods: string[];
  /** Dynamic segments */
  dynamicSegments: string[];
  isCatchAll: boolean;
}

/** A discovered Next.js layout */
export interface ExtractedLayout {
  routerType: RouterType;
  /** URL scope this layout applies to */
  urlPattern: string;
  filePath: string;
  isRoot: boolean;
  /** Parent layout's URL pattern (if nested) */
  parentPattern: string | null;
}

/** A discovered React component with server/client boundary info */
export interface ExtractedComponent {
  filePath: string;
  isServerComponent: boolean;
  isClientComponent: boolean;
  /** Whether the component is exported (default or named) */
  exported: boolean;
  /** Simple name from file stem */
  name: string;
}

export interface NextExtractionResult {
  screens: ExtractedScreen[];
  apiRoutes: ExtractedAPIRoute[];
  layouts: ExtractedLayout[];
  components: ExtractedComponent[];
}
