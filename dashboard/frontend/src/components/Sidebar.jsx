import React, { useState, useEffect, useCallback } from "react";
import { Link, useParams } from "react-router";
import axios from "axios";
import { Button } from "./ui/button";
import { ScrollArea } from "./ui/scroll-area";
import { RefreshCw } from "lucide-react";

const Sidebar = ({ isOpen }) => { // Removed unused 'toggleSidebar'
  const [guilds, setGuilds] = useState([]);
  const [error, setError] = useState(null);
  const { guildId } = useParams();
  const [isRefreshing, setIsRefreshing] = useState(false);


  const fetchGuilds = useCallback(async () => {
    try {
      const response = await axios.get("/api/guilds");
      setGuilds(response.data);
      setError(null);
    } catch (error) {
      setError(() => {
        if (error.response) {
          const data =
            typeof error.response.data === "string"
              ? error.response.data
              : JSON.stringify(error.response.data);
          return `Failed to fetch guilds. Error: ${data}`;
        } else {
          return `Failed to fetch guilds. Error: ${error.message}`;
        }
      });
      if (error.response && error.response.status === 401) {
        // Redirect to login if not authorized
        window.location.href = "/login";
      }
    }
  }, []); // No external dependencies, so empty array is fine for useCallback

  useEffect(() => {
    fetchGuilds();
  }, [fetchGuilds]); // Added fetchGuilds to dependency array

  const handleRefresh = async () => {
    setIsRefreshing(true);
    try {
      await axios.post("/api/guilds/refresh");
      await fetchGuilds();
    } catch (error) { // Renamed 'err' to 'error'
      setError("Failed to refresh guilds.");
      console.error("Failed to refresh guilds:", error); // Log the error for debugging
    } finally {
      setIsRefreshing(false);
    }
  };

  return (
    <div
      className={`fixed inset-y-0 left-0 z-50 w-64 transform bg-background p-4 transition-transform duration-300 ease-in-out md:relative md:z-auto md:translate-x-0 ${
        isOpen ? "translate-x-0" : "-translate-x-full"
      }`}
    >
      <div className="flex items-center justify-between p-4">
        <h2 className="text-xl font-semibold">Your Guilds</h2>
        <Button
          onClick={handleRefresh}
          disabled={isRefreshing}
          variant="ghost"
          size="icon"
        >
          <RefreshCw
            className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`}
          />
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <nav className="p-2">
          {error && <p className="p-2 text-red-500">{error}</p>}
          <ul>
            {guilds.map((guild) => (
              <li key={guild.id}>
                <Link
                  to={`/${guild.id}`}
                  className={`flex items-center gap-2 rounded-md p-2 transition-colors hover:bg-muted ${
                    guildId === guild.id ? "bg-muted" : ""
                  }`}
                >
                  {guild.icon ? (
                    <img
                      src={`https://cdn.discordapp.com/icons/${guild.id}/${guild.icon}.png?size=32`}
                      alt={guild.name}
                      className="h-8 w-8 rounded-full"
                    />
                  ) : (
                    <div className="h-8 w-8 rounded-full bg-gray-700 flex items-center justify-center text-white text-sm font-bold">
                      {guild.name.charAt(0).toUpperCase()}
                    </div>
                  )}
                  <span className="truncate">{guild.name}</span>
                </Link>
              </li>
            ))}
          </ul>
        </nav>
      </ScrollArea>
      <div className="p-4">
        <Button asChild className="w-full">
          <a
            href={`https://discord.com/api/oauth2/authorize?client_id=${import.meta.env.VITE_DISCORD_CLIENT_ID}&permissions=8&scope=bot%20applications.commands`}
            target="_blank"
            rel="noopener noreferrer"
          >
            Add Server
          </a>
        </Button>
      </div>
    </div>
  );
};

export default Sidebar;
