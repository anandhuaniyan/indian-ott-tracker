import React from "react";

const normalizedItems = (languages = []) => languages.map((item) => (
  Array.isArray(item)
    ? { code: item[0], name: item[1] }
    : item
));

export default function LanguageTabs({
  value = "all",
  languages = [],
  onChange,
  label = "Filter movies by language",
  className = "",
}) {
  const items = normalizedItems(languages);
  const indian = items.filter(
    (item) => item.region === "indian" && item.group === "primary",
  );
  const international = items.filter(
    (item) => item.region === "international" && item.group === "primary",
  );
  const indianCodes = new Set(indian.map((item) => item.code));
  const activeTop = indianCodes.has(value) ? "indian" : value;
  const top = [
    { code: "all", name: "All" },
    { code: "indian", name: "Indian" },
    ...international,
  ];
  const indianTabs = [
    { code: "indian", name: "All Indian" },
    ...indian,
  ];
  const handleKeyDown = (event, tabItems, index) => {
    let nextIndex;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % tabItems.length;
    if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabItems.length) % tabItems.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = tabItems.length - 1;
    if (nextIndex === undefined) return;

    event.preventDefault();
    const target = event.currentTarget.parentElement?.querySelectorAll('[role="tab"]')[nextIndex];
    target?.focus();
    onChange(tabItems[nextIndex].code);
  };
  const renderTab = (item, active, tabItems, index) => {
    return (
      <button
        key={item.code}
        type="button"
        role="tab"
        aria-selected={active}
        aria-expanded={tabItems === top && item.code === "indian" ? activeTop === "indian" : undefined}
        tabIndex={active ? 0 : -1}
        className={active ? "active" : ""}
        onClick={() => onChange(item.code)}
        onKeyDown={(event) => handleKeyDown(event, tabItems, index)}
      >
        <span>{item.name}</span>
      </button>
    );
  };

  return (
    <div className={`language-filter ${className}`.trim()}>
      <div className="language-filter-tabs" role="tablist" aria-label={label}>
        {top.map((item, index) => renderTab(item, activeTop === item.code, top, index))}
      </div>
      {(activeTop === "indian") && indian.length > 0 && (
        <div
          className="language-filter-tabs language-filter-subtabs"
          role="tablist"
          aria-label="Filter Indian movies by language"
        >
          {indianTabs.map((item, index) => (
            renderTab(item, value === item.code, indianTabs, index)
          ))}
        </div>
      )}
    </div>
  );
}
