# Fixture: Sample Elixir file for extractor testing
defmodule SampleApp do
  @moduledoc "Sample application module"

  import Logger
  alias SampleApp.Helper
  use GenServer
  require Integer

  def start_link(opts) do
    Logger.info("Starting sample app")
    {:ok, pid} = GenServer.start_link(__MODULE__, opts, name: __MODULE__)
    pid
  end

  def handle_call(:get_status, _from, state) do
    {:reply, state, state}
  end

  def handle_cast({:update, value}, state) do
    new_state = Helper.process(state, value)
    {:noreply, new_state}
  end

  defp validate(value) do
    String.valid?(value)
  end
end
