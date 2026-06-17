# Contributing to helix-token-cost-manager

Thank you for your interest in contributing to helix-token-cost-manager! This document provides guidelines and instructions for contributing to the project.

## Code of Conduct

Please note that this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to [maintainer email].

## Getting Started

### Prerequisites

- Python 3.9 or higher
- Git
- Virtual environment tool (venv, conda, etc.)

### Development Setup

1. **Fork the repository**
   ```bash
   # Go to https://github.com/Deathcharge/helix-token-cost-manager
   # Click "Fork" button
   ```

2. **Clone your fork**
   ```bash
   git clone https://github.com/YOUR_USERNAME/helix-token-cost-manager.git
   cd helix-token-cost-manager
   ```

3. **Add upstream remote**
   ```bash
   git remote add upstream https://github.com/Deathcharge/helix-token-cost-manager.git
   ```

4. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

5. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

6. **Install pre-commit hooks**
   ```bash
   pre-commit install
   ```

## Development Workflow

### Creating a Feature Branch

```bash
# Update main branch
git checkout main
git pull upstream main

# Create feature branch
git checkout -b feature/your-feature-name
```

### Making Changes

1. **Write code** following the style guide (see below)
2. **Add tests** for new functionality
3. **Update documentation** as needed
4. **Run tests locally** to ensure everything works

### Code Style

#### Python

- **Style Guide**: [PEP 8](https://www.python.org/dev/peps/pep-0008/)
- **Line Length**: 100 characters
- **Imports**: Organized in 3 groups (stdlib, third-party, local)
- **Type Hints**: Required for all public functions

**Formatting Tools**:
```bash
# Format code
black src/ tests/

# Check style
flake8 src/ tests/

# Type checking
mypy src/

# Sort imports
isort src/ tests/
```

#### JavaScript/TypeScript

- **Style Guide**: [Airbnb JavaScript Style Guide](https://github.com/airbnb/javascript)
- **Linter**: ESLint
- **Formatter**: Prettier

**Formatting Tools**:
```bash
# Format code
prettier --write src/

# Check style
eslint src/

# Type checking
tsc --noEmit
```

### Docstrings

Use Google-style docstrings for all public functions:

```python
def function_name(param1: str, param2: int) -> dict:
    """Brief description of what the function does.
    
    Longer description explaining the function in more detail,
    including any important behavior or edge cases.
    
    Args:
        param1: Description of param1
        param2: Description of param2
    
    Returns:
        Description of return value
    
    Raises:
        ValueError: When something is invalid
        TypeError: When type is wrong
    
    Example:
        >>> result = function_name('test', 42)
        >>> print(result)
        {'key': 'value'}
    """
    pass
```

### Testing

#### Writing Tests

```python
# tests/test_module.py
import pytest
from module_name import function_name

class TestFunctionName:
    """Test suite for function_name"""
    
    def test_basic_functionality(self):
        """Test basic functionality"""
        result = function_name('input', 42)
        assert result['key'] == 'expected_value'
    
    def test_edge_case(self):
        """Test edge case"""
        result = function_name('', 0)
        assert result is not None
    
    def test_error_handling(self):
        """Test error handling"""
        with pytest.raises(ValueError):
            function_name(None, -1)
```

#### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src tests/

# Run specific test
pytest tests/test_module.py::TestClassName::test_method_name

# Run with verbose output
pytest -v

# Run and stop on first failure
pytest -x
```

#### Coverage Requirements

- Minimum coverage: 80%
- New code must have 100% coverage
- Use `pytest-cov` to check coverage

### Committing Changes

#### Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types**:
- `feat`: A new feature
- `fix`: A bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, missing semicolons, etc.)
- `refactor`: Code refactoring without feature changes
- `perf`: Performance improvements
- `test`: Adding or updating tests
- `chore`: Build process, dependencies, etc.

**Example**:
```
feat(agent-swarm): add consensus mechanism

Implement the consensus mechanism for multi-agent coordination.
Adds support for supermajority voting and weighted consensus.

Closes #123
```

#### Making a Commit

```bash
# Stage changes
git add src/module.py tests/test_module.py

# Commit with message
git commit -m "feat(module): add new feature"

# View commit
git log --oneline
```

### Pushing Changes

```bash
# Push to your fork
git push origin feature/your-feature-name

# If branch was updated upstream, rebase
git fetch upstream
git rebase upstream/main
git push origin feature/your-feature-name --force-with-lease
```

## Submitting a Pull Request

### Before Submitting

1. **Sync with upstream**
   ```bash
   git fetch upstream
   git rebase upstream/main
   ```

2. **Run tests locally**
   ```bash
   pytest --cov=src tests/
   ```

3. **Run linting**
   ```bash
   black src/ tests/
   flake8 src/ tests/
   mypy src/
   ```

4. **Update documentation** if needed

### Creating the Pull Request

1. Go to your fork on GitHub
2. Click "New Pull Request"
3. Select `main` branch as base
4. Fill in the PR template:

```markdown
## Description
Brief description of changes

## Related Issues
Closes #123

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
- [ ] Added tests
- [ ] All tests pass
- [ ] Coverage maintained

## Checklist
- [ ] Code follows style guide
- [ ] Documentation updated
- [ ] No new warnings generated
- [ ] Tests added/updated
```

### PR Review Process

1. **Automated checks**: CI/CD pipeline runs tests and linting
2. **Code review**: Maintainers review code and provide feedback
3. **Revisions**: Make requested changes and push to same branch
4. **Approval**: PR is approved by at least one maintainer
5. **Merge**: PR is merged to main branch

## Reporting Issues

### Bug Reports

Include:
- Clear description of the bug
- Steps to reproduce
- Expected behavior
- Actual behavior
- Environment (OS, Python version, etc.)
- Error messages and stack traces

**Template**:
```markdown
## Description
[Clear description]

## Steps to Reproduce
1. [Step 1]
2. [Step 2]
3. [Step 3]

## Expected Behavior
[What should happen]

## Actual Behavior
[What actually happens]

## Environment
- OS: [e.g., Ubuntu 20.04]
- Python: [e.g., 3.9]
- Version: [e.g., 1.0.0]

## Error Message
[Error message and stack trace]
```

### Feature Requests

Include:
- Clear description of the feature
- Why it would be useful
- Possible implementation approach
- Related issues or PRs

**Template**:
```markdown
## Description
[Clear description of feature]

## Use Case
[Why this feature would be useful]

## Proposed Solution
[How you think it should work]

## Alternatives Considered
[Other approaches]
```

## Documentation

### Updating Documentation

1. Documentation files are in `docs/` directory
2. Use Markdown format
3. Keep documentation in sync with code
4. Update README.md for major changes

### Documentation Structure

```
docs/
├── ARCHITECTURE.md      # System architecture
├── API.md              # API reference
├── CONFIGURATION.md    # Configuration guide
├── DEPLOYMENT.md       # Deployment instructions
├── TESTING.md          # Testing guide
├── TROUBLESHOOTING.md  # Troubleshooting guide
└── EXAMPLES.md         # Usage examples
```

## Release Process

### Version Numbering

We use [Semantic Versioning](https://semver.org/):
- `MAJOR.MINOR.PATCH` (e.g., 1.2.3)
- MAJOR: Breaking changes
- MINOR: New features (backward compatible)
- PATCH: Bug fixes

### Creating a Release

1. Update version in `setup.py` or `pyproject.toml`
2. Update `CHANGELOG.md`
3. Create git tag: `git tag v1.2.3`
4. Push tag: `git push origin v1.2.3`
5. GitHub Actions will build and publish release

## Getting Help

- **Questions**: Use [GitHub Discussions](https://github.com/Deathcharge/helix-token-cost-manager/discussions)
- **Issues**: Use [GitHub Issues](https://github.com/Deathcharge/helix-token-cost-manager/issues)
- **Documentation**: See [docs/](docs/) directory
- **Helix Platform**: See [helix-platform](https://github.com/Deathcharge/helix-platform)

## Recognition

Contributors will be recognized in:
- `CONTRIBUTORS.md` file
- Release notes
- Project documentation

## Questions?

Feel free to reach out:
- Open an issue with your question
- Start a discussion
- Check existing issues and discussions

Thank you for contributing! 🎉
