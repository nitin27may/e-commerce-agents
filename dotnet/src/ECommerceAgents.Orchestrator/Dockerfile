# Build + publish. Context = repo root (see docker-compose.dotnet.yml).
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY dotnet/Directory.Packages.props ./
COPY dotnet/ECommerceAgents.sln ./
COPY dotnet/src/ ./src/
COPY dotnet/tests/ ./tests/
RUN dotnet publish src/ECommerceAgents.Orchestrator/ECommerceAgents.Orchestrator.csproj \
    -c Release -o /app/out /p:UseAppHost=false

# Runtime
FROM mcr.microsoft.com/dotnet/aspnet:10.0
WORKDIR /app
COPY --from=build /app/out ./
COPY agents/config ./agents/config
ENV ASPNETCORE_URLS=http://0.0.0.0:8080
EXPOSE 8080
ENTRYPOINT ["dotnet", "ECommerceAgents.Orchestrator.dll"]
