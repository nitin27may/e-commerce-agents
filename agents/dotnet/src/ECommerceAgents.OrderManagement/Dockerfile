# Build + publish. Context = repo root (see docker-compose.dotnet.yml).
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
# Directory.Build.props carries TreatWarningsAsErrors, LangVersion,
# InvariantGlobalization and VersionPrefix. Omitting it did not break the build —
# every .csproj happens to restate TargetFramework/Nullable/ImplicitUsings — so
# images silently compiled under laxer rules than CI and shipped unversioned.
COPY agents/dotnet/Directory.Build.props ./
COPY agents/dotnet/Directory.Packages.props ./
COPY agents/dotnet/ECommerceAgents.sln ./
COPY agents/dotnet/src/ ./src/
COPY agents/dotnet/tests/ ./tests/
RUN dotnet publish src/ECommerceAgents.OrderManagement/ECommerceAgents.OrderManagement.csproj \
    -c Release -o /app/out /p:UseAppHost=false

# Runtime — ship the shared YAML prompts alongside the binary so
# PromptLoader finds them without any volume mount.
FROM mcr.microsoft.com/dotnet/aspnet:10.0
# No explicit --gid/--uid: recent Ubuntu-based base images pre-bake a
# uid/gid 1000 "ubuntu" user, which collided here.
RUN groupadd --system dotnet \
 && useradd --system --gid dotnet --create-home dotnet \
 && apt-get update && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build --chown=dotnet:dotnet /app/out ./
COPY --chown=dotnet:dotnet agents/python/config ./agents/python/config
USER dotnet
ENV ASPNETCORE_URLS=http://0.0.0.0:8082
EXPOSE 8082
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -sf http://localhost:8082/health || exit 1
ENTRYPOINT ["dotnet", "ECommerceAgents.OrderManagement.dll"]
