import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, RuleValidationError } from "../api/client";
import type { AlertRule, FleetHost } from "../api/types";
import { RulesPage } from "./RulesPage";

const { fetchRules, fetchHosts, createRule, updateRule, deleteRule } = vi.hoisted(() => ({
  fetchRules: vi.fn(),
  fetchHosts: vi.fn(),
  createRule: vi.fn(),
  updateRule: vi.fn(),
  deleteRule: vi.fn(),
}));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, fetchRules, fetchHosts, createRule, updateRule, deleteRule };
});

function rule(overrides: Partial<AlertRule> = {}): AlertRule {
  return {
    id: 1,
    name: "High CPU",
    metric_name: "cpu.usage",
    operator: ">",
    threshold: 80,
    duration_seconds: 300,
    host: null,
    tag_filter: { core: null },
    enabled: true,
    created_at: 1,
    updated_at: 1,
    ...overrides,
  };
}

function fleetHost(hostname: string): FleetHost {
  return { hostname, first_seen_at: 0, last_seen_at: 0, status: "online", cpu_usage: null, memory_percent: null };
}

function renderRules() {
  return render(
    <MemoryRouter>
      <RulesPage />
    </MemoryRouter>,
  );
}

function field(label: string) {
  return screen.getByLabelText(label) as HTMLInputElement | HTMLSelectElement;
}

function type(label: string, value: string) {
  fireEvent.change(field(label), { target: { value } });
}

beforeEach(() => {
  fetchHosts.mockResolvedValue([fleetHost("web-1"), fleetHost("db-1")]);
});

afterEach(() => {
  vi.resetAllMocks();
  vi.restoreAllMocks();
});

describe("RulesPage", () => {
  it("shows a loading state first", () => {
    fetchRules.mockReturnValue(new Promise(() => {}));

    renderRules();

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("explains the empty state", async () => {
    fetchRules.mockResolvedValue([]);

    renderRules();

    expect(await screen.findByText("No alert rules yet. Create one to start alerting.")).toBeInTheDocument();
  });

  it("shows the API's error when rules can't be loaded", async () => {
    fetchRules.mockRejectedValue(new ApiError("boom", 503));

    renderRules();

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });

  it("lists each rule's condition, scope, filter, and status", async () => {
    fetchRules.mockResolvedValue([
      rule(),
      rule({
        id: 2,
        name: "Root disk",
        metric_name: "disk.usage",
        threshold: 90,
        duration_seconds: 0,
        host: "db-1",
        tag_filter: { mount: "/" },
        enabled: false,
      }),
    ]);

    renderRules();

    const cpu = await screen.findByRole("listitem", { name: "High CPU" });
    expect(within(cpu).getByText("cpu.usage > 80 for 5m")).toBeInTheDocument();
    expect(within(cpu).getByText(/All hosts · no core tag/)).toBeInTheDocument();
    expect(within(cpu).getByText("Enabled")).toBeInTheDocument();

    const disk = screen.getByRole("listitem", { name: "Root disk" });
    expect(within(disk).getByText("disk.usage > 90")).toBeInTheDocument();
    expect(within(disk).getByText(/db-1 · mount = \//)).toBeInTheDocument();
    expect(within(disk).getByText("Disabled")).toBeInTheDocument();
  });

  it("creates a rule from the form and reloads the list", async () => {
    fetchRules.mockResolvedValueOnce([]).mockResolvedValueOnce([rule({ id: 5, name: "Hot web" })]);
    createRule.mockResolvedValue(rule({ id: 5 }));
    renderRules();
    await screen.findByText(/No alert rules yet/);

    fireEvent.click(screen.getByRole("button", { name: "New rule" }));
    type("Name", "Hot web");
    type("Metric", "cpu.usage");
    type("Operator", ">=");
    type("Threshold", "90");
    type("For (seconds)", "120");
    await screen.findByRole("option", { name: "web-1" });
    type("Hosts", "web-1");
    fireEvent.click(screen.getByRole("button", { name: "Add tag filter" }));
    type("Tag 1 name", "core");
    type("Tag 1 match", "absent");
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));

    expect(await screen.findByRole("listitem", { name: "Hot web" })).toBeInTheDocument();
    expect(createRule).toHaveBeenCalledWith({
      name: "Hot web",
      metric_name: "cpu.usage",
      operator: ">=",
      threshold: 90,
      duration_seconds: 120,
      host: "web-1",
      tag_filter: { core: null },
      enabled: true,
    });
    expect(screen.queryByRole("form")).not.toBeInTheDocument();
  });

  it("shows the duration in words as you type it", async () => {
    fetchRules.mockResolvedValue([]);
    renderRules();
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));

    expect(screen.getByText("Fires after 5m in breach")).toBeInTheDocument();
    type("For (seconds)", "0");
    expect(screen.getByText("Fires on the first breaching sample")).toBeInTheDocument();
  });

  it("catches form mistakes before calling the API", async () => {
    fetchRules.mockResolvedValue([]);
    renderRules();
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));

    type("Threshold", "lots");
    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));

    const errors = await screen.findByRole("alert");
    expect(errors).toHaveTextContent("Name is required.");
    expect(errors).toHaveTextContent("Threshold must be a number.");
    expect(createRule).not.toHaveBeenCalled();
  });

  it("shows the API's field errors and keeps the form open with what was typed", async () => {
    fetchRules.mockResolvedValue([]);
    createRule.mockRejectedValue(
      new RuleValidationError([{ field: "host", message: "Unknown host: ghost (it has never reported)" }]),
    );
    renderRules();
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));
    type("Name", "x");
    type("Metric", "cpu.usage");
    type("Threshold", "1");

    fireEvent.click(screen.getByRole("button", { name: "Create rule" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Hosts: Unknown host: ghost (it has never reported)");
    expect(field("Name").value).toBe("x");
    expect(screen.getByRole("button", { name: "Create rule" })).toBeEnabled();
  });

  it("edits a rule with the form pre-filled", async () => {
    const existing = rule({ id: 3, host: "db-1", tag_filter: { mount: "/" }, enabled: false });
    fetchRules.mockResolvedValue([existing]);
    updateRule.mockResolvedValue(existing);
    renderRules();

    fireEvent.click(within(await screen.findByRole("listitem", { name: "High CPU" })).getByRole("button", { name: "Edit" }));

    expect(screen.getByRole("heading", { name: 'Edit "High CPU"' })).toBeInTheDocument();
    expect([field("Name").value, field("Threshold").value, field("For (seconds)").value]).toEqual(["High CPU", "80", "300"]);
    expect([field("Tag 1 name").value, field("Tag 1 value").value]).toEqual(["mount", "/"]);
    expect((screen.getByLabelText("Enabled") as HTMLInputElement).checked).toBe(false);
    await waitFor(() => expect(field("Hosts").value).toBe("db-1"));

    type("Threshold", "85");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(updateRule).toHaveBeenCalledTimes(1));
    expect(updateRule.mock.calls[0][0]).toBe(3);
    expect(updateRule.mock.calls[0][1]).toMatchObject({ threshold: 85, host: "db-1", tag_filter: { mount: "/" }, enabled: false });
  });

  it("keeps a rule's host selectable even if the host list didn't load", async () => {
    fetchHosts.mockRejectedValue(new ApiError("down", 503));
    fetchRules.mockResolvedValue([rule({ host: "db-1" })]);
    renderRules();

    fireEvent.click(within(await screen.findByRole("listitem", { name: "High CPU" })).getByRole("button", { name: "Edit" }));

    expect(field("Hosts").value).toBe("db-1");
    expect(screen.getByRole("option", { name: "All hosts" })).toBeInTheDocument();
  });

  it("cancel closes the form without saving", async () => {
    fetchRules.mockResolvedValue([]);
    renderRules();
    fireEvent.click(await screen.findByRole("button", { name: "New rule" }));

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("heading", { name: "New rule" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "New rule" })).toBeInTheDocument();
  });

  it("deletes a rule after confirmation and reloads", async () => {
    fetchRules.mockResolvedValueOnce([rule()]).mockResolvedValueOnce([]);
    deleteRule.mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRules();

    fireEvent.click(within(await screen.findByRole("listitem", { name: "High CPU" })).getByRole("button", { name: "Delete" }));

    expect(await screen.findByText(/No alert rules yet/)).toBeInTheDocument();
    expect(deleteRule).toHaveBeenCalledWith(1);
    expect(confirm.mock.calls[0][0]).toContain('"High CPU"');
  });

  it("does nothing if the delete isn't confirmed", async () => {
    fetchRules.mockResolvedValue([rule()]);
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderRules();

    fireEvent.click(within(await screen.findByRole("listitem", { name: "High CPU" })).getByRole("button", { name: "Delete" }));

    expect(deleteRule).not.toHaveBeenCalled();
    expect(screen.getByRole("listitem", { name: "High CPU" })).toBeInTheDocument();
  });

  it("reports a failed delete", async () => {
    fetchRules.mockResolvedValue([rule()]);
    deleteRule.mockRejectedValue(new ApiError("Request failed with status 503", 503));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderRules();

    fireEvent.click(within(await screen.findByRole("listitem", { name: "High CPU" })).getByRole("button", { name: "Delete" }));

    expect(await screen.findByRole("alert")).toHaveTextContent('Could not delete "High CPU": Request failed with status 503');
  });
});
