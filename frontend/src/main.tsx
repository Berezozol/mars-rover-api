import { render } from "preact";
import { App } from "./App";
import "./styles-base.css";
import "./app.css";
import "./map.css";
import "./stage19.css";
import "./data.css";
import "./solreport.css";
import "./keys.css";

const root = document.getElementById("app");
if (root) render(<App />, root);
