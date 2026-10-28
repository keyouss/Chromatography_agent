import { installObserverDiagnostics, observerDiagnostics } from './observer-diagnostics'
import { installNavigationDiagnostics, navigationDiagnostics } from './navigation-diagnostics'

installObserverDiagnostics()
installNavigationDiagnostics()
export { observerDiagnostics, navigationDiagnostics }
