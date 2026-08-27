import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./app/App";
import Tracking from "./components/Tracking";
import Consent from "./components/Consent";
import "./styles.css";
import "./v1-enhancements.css";

createRoot(document.getElementById("root")).render(<BrowserRouter><Tracking/><App /><Consent/></BrowserRouter>);
