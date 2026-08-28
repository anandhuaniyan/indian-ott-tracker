import React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Discover } from "./Public";
import { DeepSearch } from "./DeepSearch";

function SearchModes({ deep }) {
  return <nav className="search-modes" aria-label="Search mode">
    <Link className={!deep ? "active" : ""} to="/search">Search</Link>
    <Link className={deep ? "active" : ""} to="/search?mode=deep">Deep Search</Link>
  </nav>;
}

export default function SearchPage() {
  const [params] = useSearchParams();
  const deep = params.get("mode") === "deep";
  const tabs = <SearchModes deep={deep}/>;
  return deep ? <DeepSearch modeTabs={tabs}/> : <Discover modeTabs={tabs}/>;
}
