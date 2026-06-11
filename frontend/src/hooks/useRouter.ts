import { useCallback, useEffect, useState } from "react";

export type Route =
  | { view: "landing" }
  | { view: "catalogs" }
  | { view: "schemas"; catalog: string }
  | { view: "tables"; catalog: string; schema: string }
  | { view: "lineage"; table: string }
  | { view: "schemaLineage"; catalog: string; schema: string }
  | { view: "catalogLineage"; catalog: string }
  | { view: "admin" };

const ROUTE_CHANGE_EVENT = "lineage-route-change";

function parseRoute(): Route {
  const params = new URLSearchParams(window.location.search);

  if (params.get("admin") === "true") return { view: "admin" };

  const table = params.get("table");
  if (table && table.split(".").length === 3) {
    return { view: "lineage", table };
  }

  const view = params.get("view");
  if (view === "catalogs") return { view: "catalogs" };
  if (view === "schemas") {
    const catalog = params.get("catalog");
    if (catalog) return { view: "schemas", catalog };
  }
  if (view === "tables") {
    const catalog = params.get("catalog");
    const schema = params.get("schema");
    if (catalog && schema) return { view: "tables", catalog, schema };
  }
  if (view === "schemaLineage") {
    const catalog = params.get("catalog");
    const schema = params.get("schema");
    if (catalog && schema) return { view: "schemaLineage", catalog, schema };
  }
  if (view === "catalogLineage") {
    const catalog = params.get("catalog");
    if (catalog) return { view: "catalogLineage", catalog };
  }

  return { view: "landing" };
}

function routeToSearch(route: Route): string {
  switch (route.view) {
    case "landing":
      return "";
    case "catalogs":
      return "?view=catalogs";
    case "schemas":
      return `?view=schemas&catalog=${encodeURIComponent(route.catalog)}`;
    case "tables":
      return `?view=tables&catalog=${encodeURIComponent(route.catalog)}&schema=${encodeURIComponent(route.schema)}`;
    case "lineage":
      return `?table=${encodeURIComponent(route.table)}`;
    case "schemaLineage":
      return `?view=schemaLineage&catalog=${encodeURIComponent(route.catalog)}&schema=${encodeURIComponent(route.schema)}`;
    case "catalogLineage":
      return `?view=catalogLineage&catalog=${encodeURIComponent(route.catalog)}`;
    case "admin":
      return "?admin=true";
  }
}

export function navigate(route: Route, replace = false) {
  const search = routeToSearch(route);
  const url = window.location.pathname + search;
  if (replace) {
    window.history.replaceState({}, "", url);
  } else {
    window.history.pushState({}, "", url);
  }
  window.dispatchEvent(new CustomEvent(ROUTE_CHANGE_EVENT));
}

export function useRouter(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute());

  useEffect(() => {
    const sync = () => setRoute(parseRoute());
    window.addEventListener("popstate", sync);
    window.addEventListener(ROUTE_CHANGE_EVENT, sync);
    return () => {
      window.removeEventListener("popstate", sync);
      window.removeEventListener(ROUTE_CHANGE_EVENT, sync);
    };
  }, []);

  return route;
}

export const goLanding = () => navigate({ view: "landing" });
export const goCatalogs = () => navigate({ view: "catalogs" });
export const goSchemas = (catalog: string) => navigate({ view: "schemas", catalog });
export const goTables = (catalog: string, schema: string) =>
  navigate({ view: "tables", catalog, schema });
export const goLineage = (table: string) => navigate({ view: "lineage", table });
export const goSchemaLineage = (catalog: string, schema: string) =>
  navigate({ view: "schemaLineage", catalog, schema });
export const goCatalogLineage = (catalog: string) =>
  navigate({ view: "catalogLineage", catalog });
