# Build + publish. Context = repo root (see docker-compose.dotnet.yml).
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY agents/dotnet/Directory.Packages.props ./
COPY agents/dotnet/ECommerceAgents.sln ./
COPY agents/dotnet/src/ ./src/
COPY agents/dotnet/tests/ ./tests/
RUN dotnet publish src/ECommerceAgents.Orchestrator/ECommerceAgents.Orchestrator.csproj \
    -c Release -o /app/out /p:UseAppHost=false

# Runtime — ship the shared YAML prompts alongside the binary so
# PromptLoader finds them without any volume mount.
FROM mcr.microsoft.com/dotnet/aspnet:10.0
RUN groupadd --system --gid 1000 dotnet \
 && useradd --system --uid 1000 --gid dotnet --create-home dotnet
WORKDIR /app
COPY --from=build --chown=dotnet:dotnet /app/out ./
COPY --chown=dotnet:dotnet agents/python/config ./agents/python/config
USER dotnet
ENV ASPNETCORE_URLS=http://0.0.0.0:8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "ECommerceAgents.Orchestrator.dll"]
