# Production Frontend Dockerfile - Multi-stage build with nginx
FROM node:18-alpine as builder

WORKDIR /app

# Create React App inlines REACT_APP_* variables at BUILD time, so the backend
# URL must be present during this build. Supply it with
#   docker build --build-arg REACT_APP_BACKEND_URL=https://api.example.com .
# (see the `build.args` block in docker-compose*.yml). A value provided only at
# container runtime is ignored by the compiled bundle (PR #1 blocker 3).
ARG REACT_APP_BACKEND_URL
ENV REACT_APP_BACKEND_URL=$REACT_APP_BACKEND_URL

# Copy package files
COPY package.json yarn.lock ./

# Install dependencies
RUN yarn install --frozen-lockfile

# Copy application code
COPY . .

# Build for production
RUN yarn build

# Production stage - nginx
FROM nginx:alpine as production

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from builder
COPY --from=builder /app/build /usr/share/nginx/html

# Add non-root user
RUN addgroup -g 1001 -S nginx && \
    adduser -S -D -H -u 1001 -h /var/cache/nginx -s /sbin/nologin -G nginx -g nginx nginx

# Expose port
EXPOSE 3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000 || exit 1

# Start nginx
CMD ["nginx", "-g", "daemon off;"]